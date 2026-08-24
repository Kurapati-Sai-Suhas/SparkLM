"""
Generate reseed artifacts offline (M2 P2.7h-19).

    python manage.py reseed_generate --topic Array --limit 5 \\
        --exclude 2201 --out-dir ./slice-1 --operator Suhas

Dry-run by default: it plans and reports and writes nothing at all. With
`--emit` it writes files — **files only.** This command has no database write
authority, holds no writing role, and is refused on production if the
connection it is given can write anything.

It never calls `reseed_statement` or `declare_signature`. Artifacts are
reviewed by a human and applied later, by those commands, under their own
roles and their own gates.
"""

import pathlib

from django.core.management.base import BaseCommand
from django.db import connections

from groups import reseed_generation as gen
from groups import reseed_specification as spec_mod
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationBatch

#: Nothing. Every write on every table this command can see. Checked against
#: the live connection on production: a generator that CAN write is refused
#: before it reads anything, because the guarantee being offered is not "it
#: chooses not to write" but "it cannot".
GENERATOR_FORBIDDEN = tuple(
    (table, None, privilege)
    for table in ("groups_question", "groups_reseedledger",
                  "groups_remediationaction", "groups_questionpreimage",
                  "groups_remediationbatch", "groups_questionapproval",
                  "groups_referencesolution", "groups_oracleexecution",
                  "groups_codesubmission")
    for privilege in ("INSERT", "UPDATE", "DELETE"))


class Command(BaseCommand):
    help = ("Generate statement + starter artifacts for reseed candidates. "
            "Writes files, never the database. Dry-run by default.")

    def add_arguments(self, parser):
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--out-dir", required=True, metavar="PATH")
        parser.add_argument(
            "--spec-dir", required=True, metavar="PATH",
            help="Directory of <id>.spec.json operator "
                 "specifications. A candidate without one is "
                 "skipped: there is no title-only path.")
        parser.add_argument("--topic", default=None)
        parser.add_argument("--questions", metavar="IDS")
        parser.add_argument("--exclude", metavar="IDS", default="")
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument("--batch", metavar="KEY", default=None,
                            help="A FROZEN batch. Without one the artifacts "
                                 "are marked inapplicable and the reseed "
                                 "writer will refuse them.")
        parser.add_argument("--provider", default="stub",
                            choices=["stub", "gemini", "groq"])
        parser.add_argument("--attempts", type=int, default=2)
        parser.add_argument("--alias", default="default")
        parser.add_argument("--emit", action="store_true",
                            help="Write files. Without it, nothing is written.")
        parser.add_argument("--local", action="store_true")

    def handle(self, *args, **options):
        alias = options["alias"]
        operator, identity = ops.run_gates(
            alias, options["operator"],
            action="generate reseed artifacts",
            confirmed=True, require_production=not options["local"],
            needs_write=False)

        # The read-only guarantee, enforced rather than asserted.
        if identity["is_production"]:
            ops.gate_no_write_privilege(alias, GENERATOR_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "RESEED GENERATION" + ("" if options["emit"] else "  (DRY RUN)")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")

        batch = None
        if options["batch"]:
            batch = RemediationBatch.objects.using(alias).filter(
                batch_key=options["batch"]).first()
            if batch is None:
                raise ops.GateFailure(f"no such batch: {options['batch']}")

        questions = self._population(alias, options)
        if not questions:
            self.stdout.write(self.style.WARNING("No candidate selected."))
            return

        provider = {"stub": gen.StubProvider,
                    "gemini": gen.GeminiProvider,
                    "groq": gen.GroqProvider}[options["provider"]]()
        out_dir = pathlib.Path(options["out_dir"])
        spec_dir = pathlib.Path(options["spec_dir"])

        self.stdout.write(f"  provider        {provider.name} "
                          f"{provider.version}")
        self.stdout.write(f"  generator       {gen.GENERATOR_VERSION}")
        self.stdout.write(f"  prompt template {gen.PROMPT_TEMPLATE_VERSION}")
        self.stdout.write(f"  out-dir         {out_dir}")
        self.stdout.write(f"  spec-dir        {spec_dir}")
        described = (batch.batch_key if batch
                     else "(none — artifacts will be inapplicable)")
        self.stdout.write(f"  batch           {described}")
        self.stdout.write("")

        # Phase one: read. Every spec is built and frozen before a single
        # provider call, and the connection is then closed.
        #
        # Not a tidiness choice. Generation is seconds per question against a
        # remote model, and holding a pooled connection open across it made
        # the managed database close it underneath a run — the fourth
        # question died on a dropped socket. Separating the phases also makes
        # the read-only claim structural: by the time anything is generated,
        # there is no database connection left to write through.
        specs, refused = [], []
        for question in questions:
            self.stdout.write(
                f"  question {question.pk} — {question.title.strip()[:56]}")
            try:
                specification = spec_mod.load_specification(
                    spec_dir / f"{question.pk}.spec.json",
                    question_id=question.pk)
                spec = gen.build_spec(question, specification=specification,
                                      batch=batch, using=alias)
            except Exception as exc:                          # noqa: BLE001
                self.stdout.write(self.style.ERROR(f"    REFUSED  {exc}"))
                refused.append({"question_id": question.pk,
                                "status": "REFUSED", "refusals": [str(exc)],
                                "attempts": 0, "regeneration_count": 0,
                                "artifact_digest": None, "input_digest": None})
                continue
            self.stdout.write(f"    input digest    {spec.input_digest}")
            self.stdout.write(f"    band            {spec.difficulty_band}"
                              f"   topic {spec.topic}")
            specs.append(spec)

        # Not inside a transaction: a test suite wraps each test in one and
        # closing it there would tear down the harness rather than a socket.
        if options["emit"] and not connections[alias].in_atomic_block:
            connections[alias].close()

        if not options["emit"]:
            self.stdout.write(self.style.WARNING(
                "\n  DRY RUN — every candidate above is eligible; no provider "
                "was called and no file was written."))
            return

        # Phase two: generate and write files. No database access at all.
        manifests = list(refused)
        for spec in specs:
            manifests.append(self._one(spec, provider, out_dir, options))

        self._summarise(manifests)

    # ── selection ─────────────────────────────────────────────────────

    def _population(self, alias, options):
        excluded = {int(value) for value in options["exclude"].split(",")
                    if value.strip()}
        rows = Question.objects.using(alias).filter(
            content__icontains=Question.PLACEHOLDER_MARKER)
        if options["questions"]:
            ids = [int(value) for value in options["questions"].split(",")
                   if value.strip()]
            rows = rows.filter(pk__in=ids)
        if options["topic"]:
            rows = rows.filter(topic__name=options["topic"])
        rows = rows.exclude(pk__in=excluded)
        return list(rows.order_by("pk")[:options["limit"]])

    # ── one question ──────────────────────────────────────────────────

    def _one(self, spec, provider, out_dir, options):
        """Generate and validate one artifact. No database access."""
        write = self.stdout.write
        write(f"  question {spec.question_id} — {spec.title[:56]}")

        tries = gen.generate(spec, provider, attempts=options["attempts"])
        manifest = gen.write_artifacts(out_dir, spec, tries, provider)

        if manifest["status"] == gen.STATUS_READY:
            stale = gen.verify_manifest(out_dir, manifest)
            if stale:
                manifest["status"] = gen.STATUS_REJECTED
                manifest["refusals"] = stale
            else:
                write(self.style.SUCCESS(
                    f"    READY    {manifest['artifact_digest'][:16]}…  "
                    f"attempts={manifest['attempts']}"))
        if manifest["status"] != gen.STATUS_READY:
            write(self.style.ERROR(
                f"    REJECTED after {manifest['attempts']} attempt(s):"))
            for refusal in manifest["refusals"]:
                write(self.style.ERROR(f"      - {refusal}"))
        if not manifest.get("applicable"):
            write(self.style.WARNING(
                "    not applicable — generated outside a frozen batch; the "
                "reseed writer will refuse it"))
        return manifest

    def _summarise(self, manifests):
        ready = [m for m in manifests if m.get("status") == gen.STATUS_READY]
        self.stdout.write("")
        self.stdout.write(
            f"  {len(ready)} ready / {len(manifests)} considered")
        self.stdout.write(
            "Nothing was sent to reseed_statement or declare_signature. "
            "These artifacts are for human review.")
