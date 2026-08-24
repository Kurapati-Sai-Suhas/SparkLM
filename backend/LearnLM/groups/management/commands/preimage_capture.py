"""
Capture pre-images for a remediation batch (M2 P2.7, blocker J8).

    # plan only — the default, writes nothing
    python manage.py preimage_capture --batch pilot-1 --questions 1689 963 264 \\
        --purpose "P2.7 remediation pilot" --operator alice

    # capture, then STOP
    python manage.py preimage_capture ... --apply --confirm

    # a separate command, later, freezes membership
    python manage.py preimage_capture --batch pilot-1 --freeze --operator alice \\
        --apply --confirm

── This command CANNOT modify a question ──────────────────────────────────

It writes `QuestionPreImage` and `RemediationBatch` rows and nothing else. The
remediation that changes a question is a different workflow entirely, and
splitting them is the point: a tool that could capture and modify in one call
would make "did the capture succeed?" a question you can only answer after the
modification already happened.

A structural test asserts this module never writes `Question`.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationBatch


class Command(BaseCommand):
    help = ("Capture immutable pre-images for a remediation batch. Dry-run by "
            "default. Never modifies a Question.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--questions", nargs="*", type=int, default=[],
                            metavar="ID")
        parser.add_argument("--purpose", default="",
                            help="Why this batch exists. Required to create one.")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--alias", default="default")
        parser.add_argument(
            "--freeze", action="store_true",
            help="Close the batch to new members. Verifies every capture "
                 "first. A separate invocation from capture, on purpose.")
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually write. Without it this prints the plan and exits.")
        parser.add_argument(
            "--confirm", action="store_true",
            help="Required alongside --apply against production.")
        parser.add_argument(
            "--local", action="store_true",
            help="Target a non-production database. Relaxes no other gate.")

    def handle(self, *args, **options):
        alias = options["alias"]
        writing = options["apply"]

        operator, identity = ops.run_gates(
            alias, options["operator"],
            action="capture pre-images",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "PRE-IMAGE CAPTURE" + ("" if writing else "  (DRY RUN)")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")

        if options["freeze"]:
            return self._freeze(options, alias, operator, writing)
        return self._capture(options, alias, operator, writing)

    # ── capture ───────────────────────────────────────────────────────

    def _capture(self, options, alias, operator, writing):
        question_ids = sorted(set(options["questions"]))
        if not question_ids:
            raise ops.GateFailure("--questions is required when capturing")

        questions = list(Question.objects.using(alias)
                         .filter(id__in=question_ids).order_by("id"))
        missing = set(question_ids) - {q.pk for q in questions}
        if missing:
            raise ops.GateFailure(
                f"no such question(s): {sorted(missing)}; refusing to capture a "
                f"batch that does not contain what was asked for")

        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=options["batch"]).first()

        if batch is not None and batch.frozen_at is not None:
            raise ops.GateFailure(
                f"batch {batch.batch_key} was frozen at {batch.frozen_at:%Y-%m-%d %H:%M}; "
                f"membership cannot change. Capture into a NEW batch.")

        # Already-captured questions are reported, never silently re-captured:
        # the existing pre-image is the state rollback must return to.
        already = set()
        if batch is not None:
            already = set(batch.pre_images.values_list("question_id", flat=True))

        self.stdout.write(f"  batch           {options['batch']}"
                          f"{' (new)' if batch is None else ''}")
        self.stdout.write(f"  questions       {len(questions)}")
        for question in questions:
            marker = "already captured" if question.pk in already else "to capture"
            digest = pre_image.live_digest(question)
            self.stdout.write(
                f"    q{question.pk:<6} {marker:<17} digest={digest[:16]} "
                f"cases={len(question.hidden_test_cases or [])}")
        self.stdout.write("")

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        with transaction.atomic(using=alias):
            if batch is None:
                if not options["purpose"].strip():
                    raise ops.GateFailure(
                        "--purpose is required to create a batch; a batch "
                        "nobody can explain later is not an audit record")
                batch = RemediationBatch.objects.using(alias).create(
                    batch_key=options["batch"],
                    purpose=options["purpose"].strip(),
                    # By ID: see the note in pre_image.capture. The operator was
                    # resolved on `default` and this row is written on the
                    # capture alias.
                    created_by_id=operator.pk)

            captured = [pre_image.capture(batch, question, operator)
                        for question in questions]
            for record in captured:
                pre_image.verify(record)

        self.stdout.write(self.style.SUCCESS(
            f"Captured {len(captured)} pre-image(s) into {batch.batch_key}. "
            f"Every one verified."))
        self.stdout.write(self.style.WARNING(
            "Membership is NOT frozen. Run again with --freeze before any "
            "remediation. No question was modified by this command."))

    # ── freeze ────────────────────────────────────────────────────────

    def _freeze(self, options, alias, operator, writing):
        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=options["batch"]).first()
        if batch is None:
            raise ops.GateFailure(f"no such batch: {options['batch']}")

        members = list(batch.pre_images.select_related("question"))
        self.stdout.write(f"  batch           {batch.batch_key}")
        self.stdout.write(f"  members         {len(members)}")
        for record in members:
            self.stdout.write(f"    q{record.question_id:<6} "
                              f"digest={record.state_digest[:16]}")
        self.stdout.write("")

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — the batch was not frozen."))
            return

        pre_image.freeze(batch, operator)
        self.stdout.write(self.style.SUCCESS(
            f"Batch {batch.batch_key} frozen with {len(members)} member(s). "
            f"Every pre-image re-verified."))
        self.stdout.write("Membership can no longer change.")
