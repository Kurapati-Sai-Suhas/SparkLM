"""
Record a human's approval of a question's grading artifact (M2 P2.7g-3).

Writes exactly one row: a `QuestionApproval`. It does not set `trust_state`,
does not publish, does not activate the reference, and does not touch
`hidden_test_cases` or `expected_output`. A structural test enforces every one
of those, because "the command does not do that" is a claim about code, and
claims about code are checkable.

── Why approving does not promote ──────────────────────────────────────────

An approval says "I looked at artifact X and vouch for it". Promotion says
"artifact X is what is in the database right now". Those are different claims,
made at different moments, and collapsing them would mean a mistaken approval
immediately becomes trusted grading truth. Kept apart, the worst outcome of a
mistaken approval is a row that `question_promote` will refuse to act on.

    python manage.py question_approve --question 42 \\
        --digest <64 hex> --quality-report reports/q42.json \\
        --operator alice --confirm
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import _question_trust as trust
from groups.models import OracleExecution, QuestionApproval


class Command(BaseCommand):
    help = ("Record approval of a question's grading artifact. Writes a "
            "QuestionApproval row and nothing else — never trust_state.")

    def add_arguments(self, parser):
        parser.add_argument("--question", type=int, required=True, metavar="ID")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--quality-report", required=True, metavar="PATH")
        parser.add_argument(
            "--digest", required=True, metavar="SHA256",
            help="The digest shown by question_review. Checked against a "
                 "freshly recomputed one; a mismatch aborts.")
        parser.add_argument(
            "--confirm", action="store_true",
            help="Required. Approval is a permanent, attributed statement.")
        parser.add_argument(
            "--alias", default="default",
            help="Database connection. `approve` on production; the approval "
                 "row is written through it, with no fallback.")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report the plan and every reason it would be refused, "
                 "then exit. Opens no transaction and writes nothing.")

    def handle(self, *args, **options):
        alias = options["alias"]
        dry_run = options["dry_run"]
        operator = trust.resolve_operator(options["operator"])

        # Which database this IS, so the production contract applies on
        # production while the command stays usable on a test database.
        identity = ops.describe_target(alias)

        # ── Refusals ────────────────────────────────────────────────────
        #
        # Collected rather than raised one at a time, so `--dry-run` can
        # report ALL of them and an operator fixes the situation once instead
        # of discovering the next reason on the next run.
        #
        # The apply path refuses on exactly this list — there is no condition
        # the dry-run tolerates and the write does not, which is the failure
        # mode a separate "planner" implementation would eventually develop.
        problems = []

        try:
            if identity["is_production"]:
                ops.gate_writing_role(alias, allowed=ops.ALLOWED_APPROVAL_ROLES)
                ops.gate_write_privilege(alias, required=ops.APPROVAL_PROBE,
                                         forbidden=ops.APPROVAL_FORBIDDEN)
            else:
                ops.gate_write_privilege(alias, required=ops.APPROVAL_PROBE)
        except ops.GateFailure as exc:
            # A dry-run that refuses before printing anything cannot tell the
            # operator what is wrong. The verdict is reported instead — and
            # still enforced, below, for a real approval.
            if not dry_run:
                raise
            problems.append(str(exc))

        if not options["confirm"] and not dry_run:
            raise CommandError(
                "refusing to approve without --confirm: this records a "
                "permanent, attributed statement that you vouch for this "
                "question's answer key")

        question = trust.resolve_question(options["question"], alias)
        reference = trust.resolve_reference(question)
        quality = trust.load_quality_outcome(options["quality_report"])
        artifact = trust.build(question, reference, quality, using=alias)

        # Every blocker the artifact knows about: missing oracle evidence,
        # nondeterminism on record, legacy or conflicting expected outputs, a
        # failing quality gate, an unknown execution contract.
        problems.extend(artifact.blockers)

        recomputed = artifact.digest()
        supplied = (options["digest"] or "").strip().lower()
        if supplied != recomputed:
            # The artifact moved between review and approval, or the operator
            # is approving something they did not read. Both are refusals.
            problems.append(
                f"digest mismatch: you supplied {supplied or '(none)'}, the "
                f"artifact recomputes to {recomputed}. The artifact has "
                f"changed since it was reviewed, or this is not the artifact "
                f"you reviewed. Re-run question_review.")

        # ── Already approved ────────────────────────────────────────────
        #
        # Scoped to THIS digest, not to the question. The model is append-only
        # and supersession is expressed by recording a new approval, so
        # refusing "any approval exists" would break the documented way to
        # re-approve a changed artifact. What is refused is the redundant
        # case: the same artifact, vouched for twice. That row would add no
        # evidence and a second `approved_by` for the same digest is exactly
        # what an accidental re-run produces.
        duplicate = (QuestionApproval.objects.using(alias)
                     .filter(question=question, artifact_digest=recomputed)
                     .order_by("-approved_at", "-pk").first())
        if duplicate is not None:
            problems.append(
                f"approval {duplicate.pk} already records this exact artifact "
                f"({recomputed[:16]}…), approved by user "
                f"{duplicate.approved_by_id} at {duplicate.approved_at}. "
                f"Nothing has changed, so a second approval would add no "
                f"evidence. To supersede, re-approve a CHANGED artifact.")

        executed_by, executed_at = self._execution_provenance(
            question, reference, alias)

        if dry_run:
            self._render_plan(question, reference, artifact, recomputed,
                              operator, executed_by, executed_at, identity,
                              alias, problems)
            return

        if problems:
            raise CommandError(
                "refusing to approve:\n  • " + "\n  • ".join(problems))

        with transaction.atomic(using=alias):
            approval = QuestionApproval(
                question=question,
                reference=reference,
                reference_source_hash=reference.source_hash,
                artifact_digest=recomputed,
                artifact_schema_version=artifact.schema_version,
                quality_outcome=quality.as_dict(),
                # FK by ID: the operator is resolved on `default` while the
                # approval is written through the operator alias, and Django
                # refuses to relate objects it believes live on different
                # databases.
                executed_by_id=executed_by.pk if executed_by else None,
                executed_at=executed_at,
                # Reviewer and approver are the same actor in this workflow:
                # `question_review` is read-only and leaves no row, so the only
                # evidence anyone read the artifact is that they could supply
                # its digest. Recorded separately anyway (decision B5) so a
                # future four-eyes rule is a constraint, not a migration.
                reviewed_by_id=operator.pk,
                reviewed_at=timezone.now(),
                approved_by_id=operator.pk,
                approved_at=timezone.now(),
            )
            approval.full_clean(exclude=["quality_outcome"])
            approval.save(using=alias)

        self.stdout.write(self.style.SUCCESS(
            f"Recorded approval {approval.pk} for question {question.pk}."))
        self.stdout.write(f"  digest      {recomputed}")
        self.stdout.write(f"  approved by {operator.username}")
        self.stdout.write("")
        self.stdout.write(
            "Question trust_state is UNCHANGED — approval does not promote. "
            f"It is still {question.trust_state}.\n"
            "Promotion is a separate operation that independently re-derives "
            "this digest from live state:\n\n"
            f"  python manage.py question_promote --question {question.pk} "
            f"--operator <you> --confirm\n")

    def _render_plan(self, question, reference, artifact, recomputed,
                     operator, executed_by, executed_at, identity, alias,
                     problems):
        """
        The read-only approval plan.

        Everything the write would rest on, plus — explicitly — the fields it
        will not touch. "This command does not change the answer key" is a
        claim about code; the plan states it, `_MUTABLE_AFTER_CREATION` and a
        structural test enforce it.
        """
        write = self.stdout.write
        style = self.style

        write(style.MIGRATE_HEADING(
            f"DRY RUN — approval plan for question {question.pk}"))
        write(f"  alias / database    {alias} → {identity['database']} "
              f"as {identity['role']}")
        write(f"  production target   {identity['is_production']}")
        write(f"  operator            {operator.username} (id {operator.pk})")
        write("")

        write(style.MIGRATE_HEADING("What is being vouched for"))
        write(f"  question state      {pre_image.live_digest(question)}")
        write(f"  artifact digest     {recomputed}")
        write(f"  artifact schema     v{artifact.schema_version}")
        write(f"  execution contract  {artifact.execution_contract_version}")
        write(f"  status / trust      {question.status} / {question.trust_state}")
        write(f"  canonical reference #{reference.pk} {reference.language} "
              f"{reference.review_state} active={reference.is_active}")
        write(f"  reference hash      {reference.source_hash}")
        write("")

        backed = sum(1 for case in artifact.cases if case.is_oracle_backed)
        runs = sorted({case.agreeing_runs for case in artifact.cases})
        write(style.MIGRATE_HEADING("Oracle coverage"))
        write(f"  cases               {len(artifact.cases)}")
        write(f"  oracle-backed       {backed}/{len(artifact.cases)}")
        write(f"  agreeing runs       {runs}")
        write(f"  executed by         "
              f"{executed_by.username if executed_by else '(unknown)'}")
        write(f"  first executed at   {executed_at or '(none)'}")
        write("")

        quality = artifact.quality
        write(style.MIGRATE_HEADING("Quality gate"))
        write(f"  tier 1 / tier 2     {quality.tier1_kill_rate} / "
              f"{quality.tier2_kill_rate}")
        write(f"  verdict             {'PASS' if quality.passed else 'FAIL'}")
        write("")

        write(style.MIGRATE_HEADING("Proposed QuestionApproval row"))
        for name, value in (
                ("question_id", question.pk),
                ("reference_id", reference.pk),
                ("reference_source_hash", reference.source_hash),
                ("artifact_digest", recomputed),
                ("artifact_schema_version", artifact.schema_version),
                ("quality_outcome", "frozen verdict "
                    f"({'PASS' if quality.passed else 'FAIL'}, "
                    f"{len(quality.blockers)} blocker(s))"),
                ("executed_by_id", executed_by.pk if executed_by else None),
                ("executed_at", executed_at),
                ("reviewed_by_id", operator.pk),
                ("approved_by_id", operator.pk),
                ("promoted_at", "NULL — set only by question_promote"),
                ("promoted_by_id", "NULL — set only by question_promote")):
            write(f"  {name:<24}{value}")
        write("")

        write(style.MIGRATE_HEADING("Fields that will change"))
        write("  groups_questionapproval   1 row INSERTed")
        write("")
        write(style.MIGRATE_HEADING("Fields guaranteed unchanged"))
        for name in pre_image.CAPTURED_FIELDS:
            write(f"  groups_question.{name}")
        write("  groups_referencesolution  (no write privilege on this role)")
        write("  groups_oracleexecution    (no write privilege on this role)")
        write("")

        if problems:
            write(style.ERROR(
                f"NOT READY — {len(problems)} reason(s) this approval would "
                f"be refused:"))
            for problem in problems:
                write(style.ERROR(f"  • {problem}"))
        else:
            write(style.SUCCESS(
                "READY — re-run without --dry-run and with --confirm to "
                "record this approval."))

    def _execution_provenance(self, question, reference, alias):
        """
        Who ran the oracle for this reference revision, and when.

        Read on the same connection as the artifact's evidence, not `default`
        — the row this names is the row the digest was computed from.

        Best-effort: `executor` is free-form JSON written by `oracle_execute`,
        and older rows may predate the operator key. Recorded as NULL rather
        than guessed — an unknown actor is a fact, an invented one is not.
        """
        execution = (OracleExecution.objects.using(alias)
                     .filter(question=question,
                             reference_source_hash=reference.source_hash)
                     .order_by("executed_at", "pk").first())
        if execution is None:
            return None, None

        username = (execution.executor or {}).get("operator")
        user = None
        if username:
            from django.contrib.auth import get_user_model
            user = get_user_model().objects.filter(username=username).first()
        return user, execution.executed_at
