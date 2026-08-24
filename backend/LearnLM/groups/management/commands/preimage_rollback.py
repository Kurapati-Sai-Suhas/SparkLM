"""
Restore a remediation batch from its pre-images (M2 P2.7, blocker J8).

    python manage.py preimage_rollback --batch pilot-1 --operator alice
    python manage.py preimage_rollback --batch pilot-1 --operator alice \\
        --apply --confirm

Dry-run by default, atomic when applied: every pre-image is verified and every
live state checked for divergence before a single row is written, and any
failure reverts the whole batch.

── Scope ───────────────────────────────────────────────────────────────────

Restores QUESTION state only — `pre_image.ROLLBACK_SCOPE`. References, oracle
executions, approvals, submissions and recommendation logs are untouched,
because they record things that HAPPENED and undoing the data does not un-happen
them. A rollback appends one `RemediationAction` saying what it restored; it
deletes nothing.

── There is no --force ─────────────────────────────────────────────────────

`--allow-divergence` is narrow and named for exactly what it permits: restoring
over a question that changed AFTER the remediation. It cannot override a
corrupt pre-image — that stays refused — and the override is written into the
audit record so it is visible afterwards.

── The privilege contract is derived, not fixed ────────────────────────────

A restore writes back whatever a repair changed, so this command computes the
DIFFERING fields first and then demands UPDATE on exactly those columns —
`hidden_test_cases` to undo an answer-form repair, `content` to undo a
statement repair. Everything captured but not being restored is forbidden, so
an over-granted role is refused too.

That ordering matters: the plan is computed BEFORE any transaction opens, so a
missing privilege is a refusal rather than a half-finished restore. The command
previously named no role list at all and inherited the capture defaults —
demanding `learnlm_preimage_rw`, which holds nothing on `groups_question` and
therefore could never perform a rollback.
"""

from django.core.management.base import BaseCommand

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import RemediationBatch


class Command(BaseCommand):
    help = ("Atomically restore a remediation batch from its pre-images. "
            "Dry-run by default. Restores question state only.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--questions", nargs="*", type=int, default=None,
                            metavar="ID",
                            help="Restore a subset. All-or-nothing still "
                                 "applies to whatever is selected.")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--alias", default="default")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--local", action="store_true")
        parser.add_argument(
            "--allow-divergence", action="store_true",
            help="Restore over questions edited AFTER the remediation. Does "
                 "NOT override a corrupt pre-image, and is recorded in the "
                 "audit trail.")

    def handle(self, *args, **options):
        alias = options["alias"]
        writing = options["apply"]

        operator = ops.resolve_operator(options["operator"])
        identity = ops.gate_production_target(
            alias, require_production=not options["local"])

        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=options["batch"]).first()
        if batch is None:
            raise ops.GateFailure(f"no such batch: {options['batch']}")

        self.stdout.write(self.style.MIGRATE_HEADING(
            "PRE-IMAGE ROLLBACK" + ("" if writing else "  (DRY RUN)")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")
        self.stdout.write(f"  batch           {batch.batch_key} ({batch.state})")

        # The plan first: the gate below authorises the EXACT columns it names,
        # and nothing is written until every one of them is permitted.
        targets = pre_image.rollback_plan(batch, options["questions"])
        required = pre_image.required_column_writes(targets)
        required, forbidden = ops.rollback_privileges(required)
        self._render_plan(targets, required)

        if writing:
            if identity["is_production"]:
                ops.gate_writing_role(alias, allowed=ops.ALLOWED_ROLLBACK_ROLES)
                ops.gate_write_privilege(alias, required=required,
                                         forbidden=forbidden)
            else:
                ops.gate_write_privilege(alias, required=required)
            ops.require_confirmation(options["confirm"],
                                     "roll back grading truth", identity)

        self.stdout.write("")
        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was restored. Re-run with --apply --confirm."))
            return

        restored = pre_image.rollback(
            batch, operator,
            questions=options["questions"],
            allow_divergence=options["allow_divergence"])

        self.stdout.write(self.style.SUCCESS(
            f"Restored {len(restored)} question(s): {sorted(restored)}"))
        self.stdout.write(
            "Question state only. References, oracle executions, approvals and "
            "submissions were not touched; one audit record per question was "
            "appended.")

    def _render_plan(self, targets, required):
        """What rollback would do, and what would stop it."""
        write = self.stdout.write
        write(f"  to restore      {len(targets)}")
        for target in targets:
            notes = []
            if target.corrupt:
                notes.append("PRE-IMAGE CORRUPT — rollback will refuse")
            if target.diverged:
                notes.append("DIVERGED since remediation")
            if target.already_restored:
                notes.append("already at the captured state")

            write(f"    q{target.record.question_id:<6} "
                  f"{target.live_digest[:16]} -> "
                  f"{target.record.state_digest[:16]}"
                  + (f"   [{'; '.join(notes)}]" if notes else ""))
            write(f"      differing fields   {list(target.fields) or 'none'}")

        write("")
        write("  required privileges (derived from the fields above):")
        for table, column, privilege in required:
            write(f"    {privilege} ({column}) on {table}")
        if not required:
            write("    none — nothing differs, so nothing would be written")
