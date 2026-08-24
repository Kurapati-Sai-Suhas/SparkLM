"""
Inspect a remediation batch (M2 P2.7, blocker J8).

    python manage.py preimage_inspect --batch pilot-1 --operator alice

READ-ONLY. No `--apply`, no `--confirm`, no write path — the command that
answers "what would rollback do?" must be safe to run at any moment, including
mid-incident when nobody is certain what state anything is in.

It re-verifies every pre-image and compares live state against the last
recorded action, so it reports divergence BEFORE a rollback is attempted rather
than as the reason one failed.
"""

from django.core.management.base import BaseCommand

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch


class Command(BaseCommand):
    help = "Read-only report on a remediation batch and its restorability."

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--alias", default="default")
        parser.add_argument("--local", action="store_true")

    def handle(self, *args, **options):
        alias = options["alias"]
        # needs_write=False: no role gate, no confirmation. Reading is always
        # permitted, including as the census role.
        operator, identity = ops.run_gates(
            alias, options["operator"], action="inspect",
            confirmed=False, require_production=not options["local"],
            needs_write=False)

        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=options["batch"]).first()
        if batch is None:
            raise ops.GateFailure(f"no such batch: {options['batch']}")

        self.stdout.write(self.style.MIGRATE_HEADING(
            "REMEDIATION BATCH (READ-ONLY)"))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")
        self.stdout.write(f"  batch           {batch.batch_key}")
        self.stdout.write(f"  state           {batch.state}")
        self.stdout.write(f"  purpose         {batch.purpose[:60]}")
        self.stdout.write(f"  created         {batch.created_at:%Y-%m-%d %H:%M} "
                          f"by {batch.created_by.username}")
        self.stdout.write(
            f"  frozen          "
            + (f"{batch.frozen_at:%Y-%m-%d %H:%M}" if batch.frozen_at
               else "NOT FROZEN — remediation is not authorised"))
        self.stdout.write("")

        members = list(batch.pre_images.select_related("question"))
        restorable = True
        for record in members:
            try:
                pre_image.verify(record)
                integrity = "ok"
            except pre_image.DigestMismatch:
                integrity = "CORRUPT"
                restorable = False

            question = Question.objects.using(alias).get(pk=record.question_id)
            latest = (RemediationAction.objects.using(alias)
                      .filter(batch=batch, question_id=question.pk)
                      .order_by("-applied_at", "-pk").first())

            if latest is None:
                drift = "unmodified"
            elif pre_image.live_digest(question) == latest.post_digest:
                drift = "as remediated"
            else:
                drift = "DIVERGED since remediation"
                restorable = False

            self.stdout.write(
                f"  q{record.question_id:<6} pre-image {integrity:<8} "
                f"{drift:<26} captured {record.captured_at:%Y-%m-%d %H:%M} "
                f"by {record.captured_by.username}")

        self.stdout.write("")
        actions = list(RemediationAction.objects.using(alias)
                       .filter(batch=batch).order_by("applied_at", "pk"))
        self.stdout.write(f"  actions         {len(actions)}")
        for action in actions:
            self.stdout.write(
                f"    {action.applied_at:%Y-%m-%d %H:%M} "
                f"{action.action_class:<24} q{action.question_id} "
                f"by {action.applied_by.username}")

        self.stdout.write("")
        if restorable:
            self.stdout.write(self.style.SUCCESS(
                "Every pre-image verifies and no question has diverged; this "
                "batch is restorable."))
        else:
            self.stdout.write(self.style.ERROR(
                "This batch is NOT cleanly restorable — see CORRUPT/DIVERGED "
                "above. Rollback will refuse rather than guess."))
