"""
Coordinate stages 1-2 of a reseed slice (M2 P2.7h-18).

    python manage.py reseed_orchestrate --alias reseed \\
        --batch reseed-slice-1 --content-dir ./slice-1 --limit 5 \\
        --operator Suhas                        # dry run, writes nothing

    ... --statement-alias remediate --signature-alias boilerplate \\
        --apply --confirm

── What it does, and the two things it deliberately cannot do ──────────────

    PENDING -> statement generation -> verify -> signature declaration
            -> verify -> SIGNATURE_WRITTEN

It stops at SIGNATURE_WRITTEN. Contract selection (`reseed_contract`),
hidden-test expansion, oracle, approval, promotion and publication are NOT
driven from here; they keep their existing authorities and their existing
commands, and the ones that establish trust stay deliberate.

── Why the terminal is not COMPLETE (M2 P2.7h-27) ──────────────────────────

It used to be. The contract stage now sits between signature declaration and
suite authoring, so a question with both writes landed is at
SIGNATURE_WRITTEN, not COMPLETE — there is a third reseed write, and this
command does not perform it. Naming the terminal explicitly rather than
reusing COMPLETE keeps the orchestrator honest about how far it actually got:
it reports what it achieved, not what remains.

**It authors nothing.** Statements and starters are read from `--content-dir`
as `<id>.statement.html` and `<id>.starter.py`. Generation is somebody else's
problem, deliberately: a coordinator that could also invent content would be
the single component able to decide both what a question asks and that it was
asked correctly.

**It writes no question.** Its own alias is the ledger role, which holds no
UPDATE on any question column. Each stage is performed by the command that
owns that column, under that command's own alias and gates.

── Why it re-derives instead of trusting the ledger ────────────────────────

The ledger records which stage to attempt. What actually happened is re-read
every time from the two records the coordinator cannot write: the question
itself, and the append-only action trail. If those two disagree — a
placeholder gone with no `STATEMENT_GENERATION` recorded, say — the question
is refused rather than advanced. That is why the ledger carries no digest:
there is nothing on it a writer could trust instead of live state.
"""

import pathlib

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from groups import pre_image, reseed_authoring
from groups.management.commands import _preimage_ops as ops
from groups.models import (
    Question, QuestionPreImage, RemediationBatch, ReseedLedger,
)

#: A slice bigger than this is not a slice. The reseed is 1,141 questions and
#: the entire point of the ledger is that it proceeds in resumable pieces.
MAX_SLICE = 50

#: The furthest THIS command can take a question. Stages beyond it belong to
#: other authorities; see the module docstring.
ORCHESTRATED_TERMINAL = ReseedLedger.STAGE_SIGNATURE


class Command(BaseCommand):
    help = ("Coordinate statement generation and signature declaration for a "
            "reseed slice. Dry-run by default. Stops at SIGNATURE_WRITTEN.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument(
            "--content-dir", metavar="PATH",
            help="Directory holding <id>.statement.html and <id>.starter.py. "
                 "Required with --apply.")
        parser.add_argument("--limit", type=int, default=5, metavar="N")
        parser.add_argument("--questions", metavar="IDS",
                            help="Comma-separated ids, instead of the batch's "
                                 "own pre-imaged population.")
        parser.add_argument("--alias", default="default",
                            help="The LEDGER alias. Writes no question.")
        parser.add_argument("--statement-alias", default=None)
        parser.add_argument("--signature-alias", default=None)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--local", action="store_true")

    def handle(self, *args, **options):
        alias = options["alias"]
        writing = options["apply"]
        limit = options["limit"]

        if limit < 1 or limit > MAX_SLICE:
            raise ops.GateFailure(
                f"--limit must be between 1 and {MAX_SLICE}; {limit} is not a "
                f"slice. The reseed proceeds in resumable pieces by design.")

        operator, identity = ops.run_gates(
            alias, options["operator"],
            action="coordinate a reseed slice",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_RESEED_ROLES,
            required_privileges=ops.RESEED_LEDGER_PROBE,
            forbidden_privileges=ops.RESEED_LEDGER_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "RESEED ORCHESTRATION" + ("" if writing else "  (DRY RUN)")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")

        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=options["batch"]).first()
        if batch is None:
            raise ops.GateFailure(f"no such batch: {options['batch']}")
        if batch.state != RemediationBatch.STATE_CAPTURED:
            raise ops.GateFailure(
                f"batch {batch.batch_key} is {batch.state}, not CAPTURED. A "
                f"slice runs against a FROZEN batch: the pre-images are what "
                f"make every write in it reversible.")

        questions = self._population(alias, batch, options, limit)
        if not questions:
            self.stdout.write(self.style.WARNING(
                "No candidate in this batch. Nothing to do."))
            return

        content_dir = (pathlib.Path(options["content_dir"])
                       if options["content_dir"] else None)
        if writing and content_dir is None:
            raise ops.GateFailure("--apply requires --content-dir")

        plans = [self._plan(alias, batch, question, content_dir)
                 for question in questions]
        self._render(plans)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written, not even a ledger row."))
            return

        statement_alias = options["statement_alias"]
        signature_alias = options["signature_alias"]
        if not statement_alias or not signature_alias:
            raise ops.GateFailure(
                "--apply requires --statement-alias and --signature-alias. "
                "This command's own alias writes the ledger and holds no "
                "authority over any question column.")

        done = failed = skipped = 0
        for plan in plans:
            if not plan["eligible"]:
                skipped += 1
                continue
            if self._run(alias, batch, plan, operator, options,
                         statement_alias, signature_alias):
                done += 1
            else:
                failed += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"slice finished — {done} {ORCHESTRATED_TERMINAL}, {failed} FAILED, "
            f"{skipped} skipped"))
        self.stdout.write(
            "Stages 3-8 (hidden tests, contract, oracle, approval, promotion, "
            "publication) were NOT run and remain under their own authorities.")

    # ── planning ──────────────────────────────────────────────────────

    def _population(self, alias, batch, options, limit):
        rows = Question.objects.using(alias)
        if options["questions"]:
            ids = [int(value) for value in options["questions"].split(",")
                   if value.strip()]
            rows = rows.filter(pk__in=ids)
        else:
            captured = QuestionPreImage.objects.using(alias).filter(
                batch=batch).values_list("question_id", flat=True)
            rows = rows.filter(pk__in=list(captured))
        return list(rows.order_by("pk")[:limit])

    def _plan(self, alias, batch, question, content_dir):
        record = QuestionPreImage.objects.using(alias).filter(
            batch=batch, question=question).first()
        blockers = reseed_authoring.signature_blockers(
            question, pre_image_record=record, using=alias)
        stage, discrepancies = reseed_authoring.derive_stage(
            question, batch, using=alias)

        statement_file = signature_file = None
        if content_dir is not None:
            statement_file = content_dir / f"{question.pk}.statement.html"
            signature_file = content_dir / f"{question.pk}.starter.py"

        ledger = ReseedLedger.objects.using(alias).filter(
            batch=batch, question=question).first()

        return {
            "question": question,
            "id": question.pk,
            "digest": pre_image.live_digest(question),
            "blockers": blockers,
            "discrepancies": discrepancies,
            "eligible": not blockers and not discrepancies and record is not None,
            "pre_image": record,
            "derived_stage": stage,
            "ledger_stage": ledger.stage if ledger else None,
            "next_stage": ReseedLedger.ADVANCES.get(stage),
            "statement_action": (
                "author statement" if stage == ReseedLedger.STAGE_PENDING
                else "already written"),
            "signature_action": (
                "declare signature"
                if stage in (ReseedLedger.STAGE_PENDING,
                             ReseedLedger.STAGE_STATEMENT)
                else "already declared"),
            "statement_file": statement_file,
            "signature_file": signature_file,
            "statement_file_present": bool(
                statement_file and statement_file.is_file()),
            "signature_file_present": bool(
                signature_file and signature_file.is_file()),
        }

    def _render(self, plans):
        write = self.stdout.write
        for plan in plans:
            verdict = (self.style.SUCCESS("ELIGIBLE") if plan["eligible"]
                       else self.style.ERROR("REFUSED"))
            write(f"  question {plan['id']}  {verdict}")
            write(f"    digest          {plan['digest']}")
            write(f"    pre-image       "
                  f"{plan['pre_image'].state_digest if plan['pre_image'] else 'MISSING'}")
            write(f"    derived stage   {plan['derived_stage']}"
                  f"   ledger says {plan['ledger_stage'] or '(no row)'}")
            write(f"    projected next  {plan['next_stage'] or ORCHESTRATED_TERMINAL}")
            write(f"    statement       {plan['statement_action']}"
                  f"   file={'yes' if plan['statement_file_present'] else 'no'}")
            write(f"    signature       {plan['signature_action']}"
                  f"   file={'yes' if plan['signature_file_present'] else 'no'}")
            if plan["blockers"]:
                write(self.style.ERROR("    unsafe — refused because:"))
                for blocker in plan["blockers"]:
                    write(self.style.ERROR(f"      - {blocker}"))
            if plan["discrepancies"]:
                write(self.style.ERROR(
                    "    live state and the audit trail disagree:"))
                for item in plan["discrepancies"]:
                    write(self.style.ERROR(f"      - {item}"))
            if plan["pre_image"] is None:
                write(self.style.ERROR(
                    "      - no pre-image in this batch; nothing here is "
                    "reversible, so nothing here is writable"))
            write("")

        eligible = sum(1 for plan in plans if plan["eligible"])
        write(f"  {eligible} eligible of {len(plans)} considered")
        write("")

    # ── execution ─────────────────────────────────────────────────────

    def _run(self, alias, batch, plan, operator, options, statement_alias,
             signature_alias):
        """One question through stages 1-2, or FAILED with the reason."""
        question = plan["question"]
        row = self._ledger_row(alias, batch, question)

        try:
            stage, discrepancies = reseed_authoring.derive_stage(
                question, batch, using=alias)
            if discrepancies:
                raise ops.GateFailure("; ".join(discrepancies))

            if stage == ReseedLedger.STAGE_PENDING:
                if not plan["statement_file_present"]:
                    raise ops.GateFailure(
                        f"no statement file for {question.pk}")
                call_command(
                    "reseed_statement", batch=batch.batch_key,
                    question=question.pk,
                    content_file=str(plan["statement_file"]),
                    expect_digest=pre_image.live_digest(
                        Question.objects.using(alias).get(pk=question.pk)),
                    reason=f"reseed slice {batch.batch_key}",
                    operator=options["operator"], alias=statement_alias,
                    apply=True, confirm=True, local=options["local"],
                    stdout=self.stdout)
                self._advance(alias, row, ReseedLedger.STAGE_STATEMENT)

            question.refresh_from_db(using=alias)
            stage, discrepancies = reseed_authoring.derive_stage(
                question, batch, using=alias)
            if discrepancies:
                raise ops.GateFailure("; ".join(discrepancies))
            if stage == ReseedLedger.STAGE_PENDING:
                raise ops.GateFailure(
                    "the statement write reported success but the question is "
                    "still PENDING")

            if stage == ReseedLedger.STAGE_STATEMENT:
                if not plan["signature_file_present"]:
                    raise ops.GateFailure(
                        f"no starter file for {question.pk}")
                call_command(
                    "declare_signature", batch=batch.batch_key,
                    question=question.pk,
                    source_file=str(plan["signature_file"]),
                    expect_digest=pre_image.live_digest(
                        Question.objects.using(alias).get(pk=question.pk)),
                    reason=f"reseed slice {batch.batch_key}",
                    operator=options["operator"], alias=signature_alias,
                    apply=True, confirm=True, local=options["local"],
                    stdout=self.stdout)
                self._advance(alias, row, ReseedLedger.STAGE_SIGNATURE)

            question.refresh_from_db(using=alias)
            stage, discrepancies = reseed_authoring.derive_stage(
                question, batch, using=alias)
            if discrepancies:
                raise ops.GateFailure("; ".join(discrepancies))
            if stage != ORCHESTRATED_TERMINAL:
                raise ops.GateFailure(
                    f"both writes reported success but the derived stage is "
                    f"{stage}")

            self._advance(alias, row, ORCHESTRATED_TERMINAL)
            return True

        except Exception as exc:                      # noqa: BLE001
            # FAILED records what went wrong and advances NOTHING. Resume
            # re-derives the stage from live state, so a failure here can
            # never move a question forward by accident.
            self._fail(alias, row, f"{type(exc).__name__}: {exc}")
            self.stdout.write(self.style.ERROR(
                f"  question {question.pk} FAILED — {exc}"))
            return False

    def _ledger_row(self, alias, batch, question):
        with transaction.atomic(using=alias):
            row, _created = ReseedLedger.objects.using(alias).get_or_create(
                batch=batch, question=question)
        return row

    def _advance(self, alias, row, stage):
        row.stage = stage
        row.last_error = ""
        row.save(using=alias,
                 update_fields=["stage", "last_error", "updated_at"])

    def _fail(self, alias, row, message):
        row.stage = ReseedLedger.STAGE_FAILED
        row.last_error = message[:4000]
        row.attempts = row.attempts + 1
        row.save(using=alias, update_fields=["stage", "last_error", "attempts",
                                             "updated_at"])
