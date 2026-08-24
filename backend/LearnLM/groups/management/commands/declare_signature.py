"""
Declare a signature on a reseed stub (M2 P2.7h-18).

    python manage.py declare_signature --alias boilerplate \\
        --batch reseed-slice-1 --question 9830 \\
        --source-file starter.py --expect-digest <sha256> \\
        --reason "reseed slice 1" --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column of ONE question and records an
append-only `RemediationAction(SIGNATURE_DECLARATION)`.

── Why this is not `remediate_boilerplate`, and does not weaken it ─────────

`remediate_boilerplate` refuses added, removed, renamed or reordered
parameters, and it is right to. `execution_adapter` binds every stored hidden
case against the declared signature, so moving a signature under a live suite
silently changes what the answers mean. Its refusal message already names this
command's existence: a renamed method "is a different action class and needs
its own review."

That refusal protects questions that HAVE a suite. A reseed candidate does
not. It has a placeholder statement, zero hidden cases, no oracle execution,
no approval and DRAFT/UNVERIFIED trust — nothing is bound to `*args,
**kwargs`, so declaring a real signature cannot corrupt grading truth,
because there is none yet.

So this command's safety comes from a PRECONDITION ON STATE rather than a gate
on the diff. Every published question fails that precondition on six
independent grounds; q3309 and q1436 fail it on seven. `remediate_boilerplate`
is left exactly as it was.

The ROLE is deliberately shared. PostgreSQL grants are per column, and both
commands write `boilerplate_code`; a dedicated role would need identical
grants and would separate credentials, not capabilities. The separation that
matters is here, in the gate.
"""

import difflib
import pathlib

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import execution_adapter, pre_image, reseed_authoring
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column this action class may change.
DECLARED_FIELD = "boilerplate_code"


class Command(BaseCommand):
    help = ("Declare a real signature on a reseed stub whose starter is "
            "variadic. Dry-run by default. Changes the starter and nothing "
            "else, and refuses any question that has grading truth.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument("--language", default="python")
        parser.add_argument(
            "--source-file", required=True, metavar="PATH",
            help="File holding the declared starter. A file, not an argument: "
                 "source must not pass through shell quoting.")
        parser.add_argument(
            "--expect-digest", required=True, metavar="SHA256",
            help="The question's current state digest. REQUIRED: an "
                 "orchestrated write must prove it is acting on the state it "
                 "was planned against.")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--alias", default="default")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--local", action="store_true")

    def handle(self, *args, **options):
        alias = options["alias"]
        writing = options["apply"]
        language = options["language"]

        operator, identity = ops.run_gates(
            alias, options["operator"],
            action="declare a signature on a reseed stub",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_SIGNATURE_ROLES,
            required_privileges=ops.SIGNATURE_DECLARATION_PROBE,
            forbidden_privileges=ops.SIGNATURE_DECLARATION_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "SIGNATURE DECLARATION" + ("" if writing else "  (DRY RUN)")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")

        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=options["batch"]).first()
        if batch is None:
            raise ops.GateFailure(f"no such batch: {options['batch']}")

        question = Question.objects.using(alias).filter(
            pk=options["question"]).first()
        if question is None:
            raise ops.GateFailure(f"no such question: {options['question']}")

        # WRITE-AHEAD. Raises unless frozen, captured and verifying.
        record = pre_image.require_pre_image(batch, question)

        blockers = reseed_authoring.signature_blockers(
            question, pre_image_record=record, using=alias)
        if blockers:
            raise ops.GateFailure(
                f"question {question.pk} may not have a signature declared:"
                f"\n  - " + "\n  - ".join(blockers))

        before_state = pre_image.question_state(question)
        before_digest = pre_image.live_digest(question)
        if options["expect_digest"] != before_digest:
            raise ops.GateFailure(
                f"the question is at {before_digest} but this write was "
                f"planned against {options['expect_digest']}. Refusing: it "
                f"has moved since.")

        starters = dict(before_state[DECLARED_FIELD] or {})
        current = starters.get(language) or ""
        proposed = self._read_source(options["source_file"])
        if current == proposed:
            raise ops.GateFailure(
                "the file is byte-identical to the current starter; refusing "
                "to record a declaration that changes nothing")

        refusals = reseed_authoring.validate_signature(
            current, proposed, language=language)
        if refusals:
            raise ops.GateFailure(
                "the proposed signature was refused:\n  - "
                + "\n  - ".join(refusals))

        self._render_plan(batch, question, record, before_digest, current,
                          proposed, language)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator, starters,
                                   language, proposed, before_state,
                                   options["expect_digest"], options["reason"],
                                   record)

        self.stdout.write(self.style.SUCCESS(
            f"Signature declared for question {question.pk}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "No hidden test case was created. The suite is written later, by "
            "a different authority, and bound against this signature then.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, starters, language,
               proposed, before_state, expected_digest, reason, record):
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))

            blockers = reseed_authoring.signature_blockers(
                locked, pre_image_record=record, using=alias)
            if blockers:
                raise ops.GateFailure(
                    f"question {locked.pk} stopped being eligible between the "
                    f"plan and the write:\n  - " + "\n  - ".join(blockers))
            if pre_image.live_digest(locked) != expected_digest:
                raise ops.GateFailure(
                    "the question changed between the plan and the lock; the "
                    "write has been abandoned")

            updated = dict(starters)
            updated[language] = proposed
            setattr(locked, DECLARED_FIELD, updated)
            locked.save(using=alias, update_fields=[DECLARED_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)
            for name, value in before_state.items():
                if name == DECLARED_FIELD:
                    continue
                if after_state[name] != value:
                    raise ops.GateFailure(
                        f"{name} changed during a signature declaration; the "
                        f"write has been reverted")

            other = {key: value for key, value in
                     (after_state[DECLARED_FIELD] or {}).items()
                     if key != language}
            if other != {key: value for key, value in starters.items()
                         if key != language}:
                raise ops.GateFailure(
                    "a starter for another language changed during a "
                    "signature declaration; the write has been reverted")

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_SIGNATURE_DECLARATION,
                operator, detail=reason)
        return after_digest

    # ── input and reporting ───────────────────────────────────────────

    def _read_source(self, path):
        location = pathlib.Path(path)
        if not location.is_file():
            raise ops.GateFailure(f"no such source file: {path}")
        try:
            source = location.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ops.GateFailure(
                f"{path} is not valid UTF-8 ({exc.reason} at byte {exc.start})")
        if not source.strip():
            raise ops.GateFailure(f"{path} is empty or only whitespace")
        return source

    def _render_plan(self, batch, question, record, before_digest, current,
                     proposed, language):
        write = self.stdout.write
        declared = execution_adapter.declared_signature(proposed)
        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  field           {DECLARED_FIELD}[{language}] (the ONLY "
              f"field this command can change)")
        write(f"  hidden cases    {len(question.hidden_test_cases or [])} "
              f"(must be 0 — nothing is bound to the old shape)")
        if declared:
            name, parameters = declared
            write(f"  declaring       {name}("
                  + ", ".join(f"{p}: {a}" for p, a in parameters) + ")")
        write("")
        write("  diff:")
        diff = list(difflib.unified_diff(
            current.splitlines(), proposed.splitlines(),
            "variadic", "declared", lineterm="", n=1))
        for line in diff[:40]:
            style = (self.style.ERROR if line.startswith("-")
                     else self.style.SUCCESS if line.startswith("+")
                     else lambda text: text)
            write("    " + style(line))
        if len(diff) > 40:
            write(f"    ... {len(diff) - 40} more diff lines")
        write("")
