"""
Move one question along the status lifecycle (M2 P2.7h-8).

    python manage.py question_status --alias status \\
        --batch p27-pilot-1 --question 3309 --to PENDING_REVIEW \\
        --digest <64 hex> --reason "ready for promotion" \\
        --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column — `status` — of one question, and
records an append-only `RemediationAction`.

── Why this command exists at all ──────────────────────────────────────────

Until P2.7h-8 the four status values were a vocabulary with no graph. Nothing
in the repository ever wrote `Question.status`; there was no rule about which
value may follow which, and `question_promote` refused q3309 for being DRAFT
with nothing able to move it. This is the missing writer, and it is deliberately
NOT called a promotion: it changes availability, never trust.

── The two axes, and why they get two roles ────────────────────────────────

    status       is this question available          learnlm_status_rw
    trust_state  have its answers been proven        learnlm_promote_rw

`is_adaptive_eligible` is `PUBLISHED and ORACLE_VERIFIED`, so whoever holds
both columns can single-handedly make a question teach the adaptive model. The
two roles are disjoint and each is explicitly denied the other's column.

── What the edges require ──────────────────────────────────────────────────

DRAFT → PENDING_REVIEW carries no evidence requirement. It makes nothing
visible (status does not gate delivery — `servable_questions` filters on
content and hidden tests, never on status) and nothing eligible. Its only
effect is to satisfy the DRAFT/ORACLE_VERIFIED CHECK so promotion can run, and
promotion independently re-derives the approval, the evidence, the quality
verdict and the digest. Duplicating those checks here would put them in the one
place that cannot enforce them at the moment they matter.

PENDING_REVIEW → PUBLISHED is the edge that turns a question ON, so it requires
the whole chain: ORACLE_VERIFIED, a `QuestionApproval` whose digest still
matches a freshly rebuilt artifact, that approval stamped as promoted, and no
artifact blockers.

    NOTE — this is the one rule not already implied by the repository. The
    model's docstring describes PUBLISHED + UNVERIFIED as a legitimate "legacy"
    state, and this command forbids creating one. That state has no effect in
    this codebase (not eligible, and delivery is not status-gated), and
    requiring ORACLE_VERIFIED buys the invariant "every PUBLISHED question is
    oracle-verified". It is one condition in `_publication_blockers` if you
    want it back.

PUBLISHED → PENDING_REVIEW is withdrawal. It only ever reduces eligibility, so
it requires nothing beyond the gates.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import pre_image, question_artifact
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import _question_trust as trust
from groups.models import (
    Question, QuestionApproval, RemediationAction, RemediationBatch,
)

#: The single column this action class may change.
REPAIRABLE_FIELD = "status"

#: The status a transition may only ever be entered with full evidence.
GATED_TARGET = Question.STATUS_PUBLISHED


class Command(BaseCommand):
    help = ("Move one question between status values along the legal "
            "lifecycle. Writes `status` and nothing else. Dry-run by default.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--to", required=True, dest="target", metavar="STATUS",
            help="the status to move to; must be a legal edge from the "
                 "current one")
        parser.add_argument(
            "--digest", required=True, metavar="SHA256",
            help="the question's CURRENT state digest, as reported by the "
                 "dry-run. Checked against live state; a mismatch aborts.")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--alias", default="default")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--local", action="store_true")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Force a dry run. Wins over --apply, so a command line "
                 "carrying both writes nothing.")

    def handle(self, *args, **options):
        alias = options["alias"]
        writing = options["apply"] and not options["dry_run"]

        operator, identity = ops.run_gates(
            alias, options["operator"],
            action="change a question's status",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_STATUS_ROLES,
            required_privileges=ops.STATUS_TRANSITION_PROBE,
            forbidden_privileges=ops.STATUS_TRANSITION_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "STATUS TRANSITION" + ("" if writing else "  (DRY RUN)")))
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

        record = pre_image.require_pre_image(batch, question)

        before_state = pre_image.question_state(question)
        current = before_state[REPAIRABLE_FIELD]
        target = (options["target"] or "").strip()

        blockers = self._transition_blockers(question, current, target)
        publication_blockers, evidence = self._publication_blockers(
            question, alias, target)
        blockers.extend(publication_blockers)

        before_digest = pre_image.live_digest(question)
        supplied = (options["digest"] or "").strip().lower()
        if supplied != before_digest:
            blockers.append(
                f"digest mismatch: you supplied {supplied or '(none)'}, the "
                f"question is currently {before_digest}. Either it moved since "
                f"you looked, or this is not the question you looked at.")

        projected = pre_image.state_digest(
            question.pk, dict(before_state, **{REPAIRABLE_FIELD: target}))

        self._render_plan(batch, question, record, current, target,
                          before_digest, projected, blockers, evidence)

        if blockers:
            raise ops.GateFailure(
                f"question {question.pk} cannot move to {target!r}:\n"
                + "\n".join(f"  - {reason}" for reason in blockers)
                + "\nNothing was written.")

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator, target,
                                   before_state, options["reason"])

        self.stdout.write(self.style.SUCCESS(
            f"Question {question.pk} status is now {target}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "trust_state is UNCHANGED — this command cannot write it, and the "
            "role it runs as holds no privilege on that column.")

    # ── the rules ─────────────────────────────────────────────────────

    def _render_evidence(self, evidence):
        """What the publication edge re-derived, when there was any."""
        if not evidence:
            return
        write = self.stdout.write
        approval = evidence.get("approval")
        reference = evidence.get("reference")
        artifact = evidence.get("artifact")

        write("  evidence re-derived for publication:")
        if approval is not None:
            write(f"    approval              #{approval.pk} by user "
                  f"{approval.approved_by_id} at {approval.approved_at}")
            write(f"    promoted              {approval.promoted_at or 'NO'} "
                  f"by user {approval.promoted_by_id}")
            write(f"    approved digest       {approval.artifact_digest}")
        if artifact is not None:
            write(f"    recomputed digest     {evidence.get('recomputed')}")
            write(f"    digests match         "
                  f"{evidence.get('recomputed') == approval.artifact_digest}")
            backed = sum(1 for case in artifact.cases if case.is_oracle_backed)
            runs = sorted({case.agreeing_runs for case in artifact.cases})
            write(f"    oracle-backed cases   {backed}/{len(artifact.cases)}")
            write(f"    agreeing runs         {runs}")
            write(f"    artifact blockers     "
                  f"{artifact.blockers or 'none'}")
            quality = artifact.quality
            write(f"    quality gate          tier1 {quality.tier1_kill_rate} "
                  f"/ tier2 {quality.tier2_kill_rate} — "
                  f"{'PASS' if quality.passed else 'FAIL'}")
        if reference is not None:
            write(f"    canonical reference   #{reference.pk} "
                  f"{reference.review_state} active={reference.is_active}")
            write(f"    reference hash        {reference.source_hash}")
            write(f"    == approved hash      "
                  f"{reference.source_hash == approval.reference_source_hash}")
        write("")

    def _transition_blockers(self, question, current, target):
        """Whether this edge exists at all."""
        blockers = []
        legal = {value for value, _label in Question.STATUS_CHOICES}
        if target not in legal:
            blockers.append(
                f"{target!r} is not a status; the vocabulary is "
                f"{', '.join(sorted(legal))}")
            return blockers

        if target == current:
            blockers.append(
                f"question is already {current}; refusing to record a "
                f"transition that changes nothing")
            return blockers

        if (current, target) not in Question.STATUS_TRANSITIONS:
            allowed = sorted(to for frm, to in Question.STATUS_TRANSITIONS
                             if frm == current)
            blockers.append(
                f"{current} → {target} is not a legal transition; from "
                f"{current} the only legal move is to "
                f"{', '.join(allowed) if allowed else '(nothing)'}")
        return blockers

    def _publication_blockers(self, question, alias, target):
        """
        (blockers, evidence) for the chain PUBLISHED requires, and nothing else.

        Publication is the act that makes a verified question start teaching
        the adaptive model, so it re-derives the whole chain rather than
        trusting that promotion happened correctly.

        `evidence` is what was re-derived, returned so the plan can SHOW it. A
        preflight that silently passes its checks tells an operator only that
        nothing was wrong, which is not the same as telling them what is true.
        """
        if target != GATED_TARGET:
            return [], {}

        blockers = []
        evidence = {}
        if question.trust_state != Question.TRUST_ORACLE_VERIFIED:
            blockers.append(
                f"trust_state is {question.trust_state}, not "
                f"{Question.TRUST_ORACLE_VERIFIED}. Publishing an unverified "
                f"question would make its unproven answers teach the adaptive "
                f"model; promote it first.")

        approval = QuestionApproval.current_for(question, using=alias)
        if approval is None:
            blockers.append(
                f"question {question.pk} has no approval; nobody has vouched "
                f"for the artifact being published")
            return blockers, evidence
        evidence["approval"] = approval

        if approval.promoted_at is None:
            blockers.append(
                f"approval {approval.pk} has never been acted on "
                f"(promoted_at is NULL); publication follows promotion")

        quality = question_artifact.QualityOutcome.from_mapping(
            approval.quality_outcome or {})
        reference = trust.resolve_reference(question)

        # The reference the approval names must still be THE canonical one, at
        # the same revision. Mirrors `question_promote`'s gate 4a rather than
        # relying on the digest comparison below to notice: the digest catches
        # a reference whose SOURCE moved, because the live hash is inside it,
        # but it cannot distinguish "this approval was granted against a
        # different reference" from "these are the same". Publication is the
        # act that makes the answers count; it says which of the two it means.
        if reference.pk != approval.reference_id:
            blockers.append(
                f"the canonical reference is now #{reference.pk}, but approval "
                f"{approval.pk} was granted against #{approval.reference_id}; "
                f"a different implementation now defines this question's "
                f"answers. Re-review before publishing.")
        elif reference.source_hash != approval.reference_source_hash:
            blockers.append(
                f"reference #{reference.pk} has changed since approval "
                f"{approval.pk}: approved {approval.reference_source_hash}, "
                f"live {reference.source_hash}. The approved implementation is "
                f"not the one that would run now.")

        artifact = trust.build(question, reference, quality, using=alias)
        blockers.extend(artifact.blockers)

        evidence.update(reference=reference, artifact=artifact,
                        quality=quality, recomputed=artifact.digest())

        recomputed = artifact.digest()
        if recomputed != approval.artifact_digest:
            blockers.append(
                f"the artifact has changed since approval {approval.pk}: "
                f"approved {approval.artifact_digest}, recomputed "
                f"{recomputed}. Re-review before publishing.")
        return blockers, evidence

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, target, before_state,
               reason):
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))

            # Re-checked under the lock: everything above was decided against a
            # row read without one, and the edge that is legal depends on the
            # value another writer may have changed in between.
            if getattr(locked, REPAIRABLE_FIELD) != before_state[REPAIRABLE_FIELD]:
                raise ops.GateFailure(
                    f"status is {getattr(locked, REPAIRABLE_FIELD)!r} under "
                    f"lock, not {before_state[REPAIRABLE_FIELD]!r}; another "
                    f"writer moved it. Nothing written.")
            if locked.trust_state != before_state["trust_state"]:
                raise ops.GateFailure(
                    f"trust_state changed to {locked.trust_state!r} while this "
                    f"command was running; the transition was decided against "
                    f"a different question. Nothing written.")

            setattr(locked, REPAIRABLE_FIELD, target)
            locked.save(using=alias, update_fields=[REPAIRABLE_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)

            for name, value in before_state.items():
                if name == REPAIRABLE_FIELD:
                    continue
                if after_state[name] != value:
                    raise ops.GateFailure(
                        f"{name} changed during a status transition; the write "
                        f"has been reverted")

            if after_state[REPAIRABLE_FIELD] != target:
                raise ops.GateFailure(
                    f"the status column holds "
                    f"{after_state[REPAIRABLE_FIELD]!r} after the write, not "
                    f"{target!r}; the write has been reverted")

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_STATUS_TRANSITION,
                operator, detail=reason)
        return after_digest

    # ── reporting ─────────────────────────────────────────────────────

    def _render_plan(self, batch, question, record, current, target,
                     before_digest, projected, blockers, evidence=None):
        write = self.stdout.write
        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  projected after {projected}")
        write("")
        write(f"  current status  {current}")
        write(f"  proposed status {target}")
        write(f"  legal edges     "
              f"{', '.join(f'{a} → {b}' for a, b in sorted(Question.STATUS_TRANSITIONS))}")
        write("")

        self._render_evidence(evidence or {})

        write("  fields that would change:")
        write(f"    groups_question.{REPAIRABLE_FIELD}   {current} → {target}")
        write("  fields guaranteed unchanged:")
        for name in pre_image.CAPTURED_FIELDS:
            if name == REPAIRABLE_FIELD:
                continue
            write(f"    groups_question.{name}")
        write("    groups_questionapproval   (no write privilege on this role)")
        write("    groups_referencesolution  (no write privilege on this role)")
        write("    groups_oracleexecution    (no write privilege on this role)")
        write("")

        write("  trust and eligibility:")
        write(f"    trust_state             {question.trust_state} (unchanged)")
        write(f"    adaptive eligible now   {question.is_adaptive_eligible}")
        after = (target == Question.STATUS_PUBLISHED
                 and question.trust_state == Question.TRUST_ORACLE_VERIFIED)
        write(f"    adaptive eligible after {after}")
        write("")

        if blockers:
            write(self.style.ERROR(f"BLOCKED — {len(blockers)} reason(s):"))
            for reason in blockers:
                write(self.style.ERROR(f"  • {reason}"))
        else:
            write(self.style.SUCCESS(
                f"LEGAL — {current} → {target} may be applied."))
        write("")
