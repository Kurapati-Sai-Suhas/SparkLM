"""
Promote a question to ORACLE_VERIFIED (M2 P2.7g-3).

The first and only writer of `Question.trust_state` in this codebase. Every
gate below exists because this is the write that makes a question's answers
count toward the adaptive model — after which a wrong answer key stops being a
bad practice question and starts being a corrupted learner model.

── What it re-proves ───────────────────────────────────────────────────────

Promotion trusts NOTHING from the approval except the human judgement and the
quality evidence frozen at that moment. Everything else is rebuilt from live
state and compared:

    1. an approval exists                       (a human vouched)
    2. approval schema == current schema        (computed under these rules)
    3. recomputed digest == approved digest     (the artifact has not moved)
    4. the reference is canonical RIGHT NOW     (lifecycle, checked live)
    5. oracle evidence still resolves           (rebuilt, not read from the row)
    6. quality gate passed                      (the frozen, approved verdict)
    7. status != DRAFT                          (also a DB CHECK)

── Why the quality verdict is frozen rather than re-run ────────────────────

Re-running the P2.7h-1 gate needs Judge0. Making trust promotion depend on an
external service being reachable would mean a Judge0 outage either blocks
promotion or, worse, invites a --skip-quality flag. The verdict approved by a
human is reused instead — and suite drift is still caught, because changing any
hidden test changes its case digest, which is inside the artifact digest that
gate 3 compares.

    python manage.py question_promote --question 42 --operator alice --confirm
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from groups import pre_image, question_artifact
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import _question_trust as trust
from groups.models import Question, QuestionApproval


class Command(BaseCommand):
    help = ("Set trust_state=ORACLE_VERIFIED for a question whose approved "
            "artifact still matches live state. Refuses otherwise.")

    def add_arguments(self, parser):
        parser.add_argument("--question", type=int, required=True, metavar="ID")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument(
            "--confirm", action="store_true",
            help="Required. Promotion makes this question's verdicts teach "
                 "the adaptive model.")
        parser.add_argument(
            "--alias", default="default",
            help="Database connection. `promote` on production; the trust "
                 "write is routed through it, with no fallback.")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Re-derive everything promotion rests on, report the exact "
                 "fields it would write, and exit. Opens no transaction, "
                 "takes no lock, writes nothing.")
        # There is deliberately NO --trust-state, --force or --skip-* flag.
        # The target state is not a parameter: this command promotes, or it
        # refuses. A caller-supplied trust value would make every gate above
        # advisory.

    def handle(self, *args, **options):
        alias = options["alias"]
        dry_run = options["dry_run"]
        operator = trust.resolve_operator(options["operator"])

        # Every reason promotion would be refused, collected rather than
        # raised one at a time. `--dry-run` prints the whole list; the write
        # path refuses on exactly the same list, so there is no condition the
        # preflight tolerates and the write does not.
        blocked = []

        identity = ops.describe_target(alias)
        try:
            if identity["is_production"]:
                ops.gate_writing_role(alias,
                                      allowed=ops.ALLOWED_PROMOTION_ROLES)
                ops.gate_write_privilege(alias, required=ops.PROMOTION_PROBE,
                                         forbidden=ops.PROMOTION_FORBIDDEN)
            else:
                ops.gate_write_privilege(alias, required=ops.PROMOTION_PROBE)
        except ops.GateFailure as exc:
            # The preflight reports the verdict instead of refusing before it
            # can print anything; the write path below still enforces it.
            if not dry_run:
                raise
            blocked.append(str(exc))

        if not options["confirm"] and not dry_run:
            raise CommandError(
                "refusing to promote without --confirm: this makes the "
                "question's verdicts count toward every learner's model")

        question = trust.resolve_question(options["question"], alias)

        # 1. A human vouched.
        approval = QuestionApproval.current_for(question, using=alias)
        if approval is None:
            raise CommandError(
                f"question {question.pk} has no approval. Oracle execution, "
                f"provenance and a passing quality gate are NOT sufficient — "
                f"a human must approve the artifact first "
                f"(question_review, then question_approve).")

        # 2. Approved under the rules currently in force.
        if approval.artifact_schema_version != question_artifact.ARTIFACT_SCHEMA_VERSION:
            blocked.append(
                f"approval {approval.pk} was computed under artifact schema "
                f"v{approval.artifact_schema_version}, but this code computes "
                f"v{question_artifact.ARTIFACT_SCHEMA_VERSION}. The digest is "
                f"not comparable across schema versions. Re-review and "
                f"re-approve under the current schema.")

        # 4a. The approved reference must still exist as THE canonical one.
        reference = trust.resolve_reference(question)
        if reference.pk != approval.reference_id:
            blocked.append(
                f"the canonical reference is now {reference.pk}, but approval "
                f"{approval.pk} was granted against reference "
                f"{approval.reference_id}. A different implementation now "
                f"defines this question's answers; re-review.")
        if reference.source_hash != approval.reference_source_hash:
            blocked.append(
                f"reference {reference.pk} source hash has changed since "
                f"approval {approval.pk}; the approved implementation is not "
                f"the one that would run now.")

        # 6. The frozen quality verdict must itself be a pass.
        #
        # Checked BEFORE building the artifact, deliberately. `build_artifact`
        # also appends a quality blocker, so ordering this after it would make
        # this branch unreachable — dead code that reads like a safety gate.
        # A mutation sweep found exactly that and the check was moved here,
        # where it fires first and says something specific.
        quality = question_artifact.QualityOutcome.from_mapping(
            approval.quality_outcome or {})
        if not quality.passed:
            blocked.append(
                f"approval {approval.pk} froze a FAILING quality verdict "
                f"(tier1={quality.tier1_kill_rate}, "
                f"tier2={quality.tier2_kill_rate}, "
                f"{len(quality.blockers)} blocker(s)); refusing to promote.")

        # 3 + 5. Rebuild the artifact from live state, including re-reading
        # oracle provenance. The quality half comes from the frozen, approved
        # verdict so a fresh unreviewed one cannot be substituted.
        artifact = trust.build(question, reference, quality, using=alias)
        blocked.extend(artifact.blockers)

        recomputed = artifact.digest()
        if recomputed != approval.artifact_digest:
            blocked.append(
                f"STALE APPROVAL: approved {approval.artifact_digest}, "
                f"recomputed {recomputed}. The question, its hidden tests, its "
                f"expected outputs, its execution contract, its boilerplate, "
                f"or its oracle evidence has changed since approval "
                f"{approval.pk}. Re-review and re-approve.")

        # 7. DRAFT means the statement is still being written, so an answer key
        # proven against it proves nothing. Also enforced by a DB CHECK — this
        # is here so the operator gets a sentence instead of an IntegrityError.
        if question.status == Question.STATUS_DRAFT:
            blocked.append(
                f"question {question.pk} is DRAFT. A draft cannot hold a "
                f"proven answer key; advance its status first.")

        already = question.trust_state == Question.TRUST_ORACLE_VERIFIED

        if dry_run:
            self._render_preflight(question, reference, approval, artifact,
                                   recomputed, operator, identity, alias,
                                   blocked, already)
            return

        if already:
            self.stdout.write(self.style.WARNING(
                f"question {question.pk} is already ORACLE_VERIFIED; nothing "
                f"to do."))
            return

        if blocked:
            raise CommandError(
                "refusing to promote:\n  • " + "\n  • ".join(blocked))

        with transaction.atomic(using=alias):
            # ── Re-read under a row lock ────────────────────────────────
            #
            # Everything above was read without one. Between those reads and
            # this write another connection can land a remediation, retire the
            # reference, or promote this same question — and the losing writer
            # would set a trust state derived from a question that no longer
            # exists in that form. The lock closes that window, and the digest
            # is recomputed INSIDE it so the value being trusted is the value
            # under lock, not the one read a moment earlier.
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))
            stamped = (QuestionApproval.objects.using(alias)
                       .select_for_update().get(pk=approval.pk))

            if stamped.promoted_at is not None:
                raise CommandError(
                    f"approval {stamped.pk} was promoted at "
                    f"{stamped.promoted_at} by user {stamped.promoted_by_id} "
                    f"while this command was running; refusing to stamp it "
                    f"twice.")
            if locked.trust_state != Question.TRUST_UNVERIFIED:
                raise CommandError(
                    f"question {locked.pk} is {locked.trust_state} under lock, "
                    f"not UNVERIFIED; another writer moved it. Nothing "
                    f"written.")
            if locked.status == Question.STATUS_DRAFT:
                raise CommandError(
                    f"question {locked.pk} is DRAFT under lock; nothing "
                    f"written.")

            confirmed = trust.build(locked, trust.resolve_reference(locked),
                                    quality, using=alias)
            if confirmed.blockers or confirmed.digest() != approval.artifact_digest:
                raise CommandError(
                    f"the artifact changed between preflight and write:\n"
                    f"  approved:   {approval.artifact_digest}\n"
                    f"  under lock: {confirmed.digest()}\n"
                    f"Nothing written.")

            locked.trust_state = Question.TRUST_ORACLE_VERIFIED
            # update_fields, so promotion cannot carry an unrelated in-memory
            # edit to content or hidden tests into the database alongside it.
            locked.save(using=alias, update_fields=["trust_state"])

            stamped.promoted_at = timezone.now()
            stamped.promoted_by_id = operator.pk
            stamped.save(using=alias,
                         update_fields=["promoted_at", "promoted_by"])
            approval = stamped

        self.stdout.write(self.style.SUCCESS(
            f"Question {locked.pk} is now ORACLE_VERIFIED."))
        self.stdout.write(f"  approval    {approval.pk}")
        self.stdout.write(f"  digest      {recomputed}")
        self.stdout.write(f"  promoted by {operator.username}")
        self.stdout.write("")
        self.stdout.write(
            "Submissions recorded from now on may teach the adaptive model, "
            "if the question is also PUBLISHED. Past submissions are "
            "unaffected — adaptive_eligible is frozen at submission time.")

    def _render_preflight(self, question, reference, approval, artifact,
                          recomputed, operator, identity, alias, blocked,
                          already):
        """
        Everything promotion rests on, re-derived, and the exact fields it
        would write. Reads only — no transaction, no lock.
        """
        write = self.stdout.write
        style = self.style

        write(style.MIGRATE_HEADING(
            f"PREFLIGHT — promotion plan for question {question.pk}"))
        write(f"  alias / database    {alias} → {identity['database']} "
              f"as {identity['role']}")
        write(f"  production target   {identity['is_production']}")
        write(f"  operator            {operator.username} (id {operator.pk})")
        write("")

        write(style.MIGRATE_HEADING("The approval being acted on"))
        write(f"  approval            #{approval.pk}")
        write(f"  approved by / at    user {approval.approved_by_id} / "
              f"{approval.approved_at}")
        write(f"  approved digest     {approval.artifact_digest}")
        write(f"  recomputed digest   {recomputed}")
        write(f"  match               {recomputed == approval.artifact_digest}")
        write(f"  artifact schema     v{approval.artifact_schema_version} "
              f"(code computes v{question_artifact.ARTIFACT_SCHEMA_VERSION})")
        write(f"  already promoted    {approval.promoted_at or 'no'}")
        write("")

        write(style.MIGRATE_HEADING("Re-derived from live state"))
        write(f"  question state      {pre_image.live_digest(question)}")
        write(f"  status / trust      {question.status} / "
              f"{question.trust_state}")
        write(f"  execution contract  {artifact.execution_contract_version}")
        write(f"  canonical reference #{reference.pk} {reference.review_state} "
              f"active={reference.is_active}")
        write(f"  reference hash      {reference.source_hash}")
        write(f"  == approved hash    "
              f"{reference.source_hash == approval.reference_source_hash}")

        backed = sum(1 for case in artifact.cases if case.is_oracle_backed)
        runs = sorted({case.agreeing_runs for case in artifact.cases})
        write(f"  cases               {len(artifact.cases)}")
        write(f"  oracle-backed       {backed}/{len(artifact.cases)}")
        write(f"  agreeing runs       {runs}")
        write(f"  quality (frozen)    tier1 {artifact.quality.tier1_kill_rate} "
              f"/ tier2 {artifact.quality.tier2_kill_rate} — "
              f"{'PASS' if artifact.quality.passed else 'FAIL'}")
        write("")

        write(style.MIGRATE_HEADING("Fields promotion would write"))
        write(f"  groups_question.trust_state          "
              f"{question.trust_state} → {Question.TRUST_ORACLE_VERIFIED}")
        write(f"  groups_questionapproval.promoted_at  NULL → now()")
        write(f"  groups_questionapproval.promoted_by  NULL → {operator.pk}")
        write("")
        write(style.MIGRATE_HEADING("Fields promotion would NOT write"))
        write("  groups_question.status               "
              f"stays {question.status} — promotion does not publish")
        for name in ("title", "content", "base_difficulty", "topic_id",
                     "hidden_test_cases", "boilerplate_code",
                     "hidden_wrapper_code", "execution_contract_version"):
            write(f"  groups_question.{name}")
        write("  groups_referencesolution             (no write privilege)")
        write("  groups_oracleexecution               (no write privilege)")
        write("")

        write(style.MIGRATE_HEADING("Adaptive eligibility"))
        write(f"  now                 {question.is_adaptive_eligible}")
        eligible_after = (question.status == Question.STATUS_PUBLISHED)
        write(f"  after promotion     {eligible_after}"
              f"{'' if eligible_after else '  (needs PUBLISHED as well)'}")
        write("  Past submissions are unaffected — the flag is frozen onto "
              "each submission at the moment it is recorded.")
        write("")

        if already:
            write(style.WARNING(
                f"Question {question.pk} is ALREADY ORACLE_VERIFIED; a real "
                f"run would report nothing to do and write nothing."))
            return
        if blocked:
            write(style.ERROR(
                f"NOT PROMOTABLE — {len(blocked)} reason(s):"))
            for reason in blocked:
                write(style.ERROR(f"  • {reason}"))
        else:
            write(style.SUCCESS(
                "PROMOTABLE — re-run without --dry-run and with --confirm."))
