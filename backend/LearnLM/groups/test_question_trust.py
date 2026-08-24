"""
Question-level trust: artifact, approval, promotion (M2 P2.7g-3).

The suite is deliberately lopsided toward refusals. `trust_state` has had no
writer at all until this phase, and the one being added is the write that lets
a question's verdicts teach the adaptive model. The interesting question is
never "does promotion work" — it is "what can reach ORACLE_VERIFIED without a
human having approved exactly what is in the database".

Judge0 is never contacted. All oracle evidence is written through the real
`provenance.record_execution` path against synthetic data.
"""

import ast
import importlib
import inspect
import json
import pathlib
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from groups import provenance, question_artifact
from groups.conftest import approved_reference
from groups.models import (
    CodingPortal, OracleExecution, Question, QuestionApproval, Topic,
)
from groups.question_artifact import (
    ARTIFACT_SCHEMA_VERSION, CaseEvidence, QualityOutcome, build_artifact,
)

SOURCE = "print(int(input()) * 2)\n"


def passing_quality(mutants=("m1", "m2", "m3")):
    return QualityOutcome(tier1_kill_rate=1.0, tier2_kill_rate=0.9,
                          blockers=(), mutant_identifiers=tuple(mutants))


def failing_quality():
    return QualityOutcome(tier1_kill_rate=0.5, tier2_kill_rate=0.2,
                          blockers=("tier 1 kill rate below 1.0",),
                          mutant_identifiers=("m1",))


class TrustTestCase(TestCase):
    """One question, one canonical reference, full oracle evidence."""

    def setUp(self):
        User = get_user_model()
        self.operator = User.objects.create_user(
            username="operator", email="op@t.test", password="Pv#2026xyz",
            is_staff=True)
        self.learner = User.objects.create_user(
            username="learner", email="l@t.test", password="Pv#2026xyz")

        portal = CodingPortal.objects.create(name="Trust Portal")
        self.topic = Topic.objects.create(
            name="TrustTopic", structure_type="flat", portal=portal)
        self.question = self._make_question()
        self.reference = approved_reference(self.question, source_code=SOURCE)
        self.reference.refresh_from_db()
        self._record_evidence()

    # ── fixture helpers ───────────────────────────────────────

    def _cases(self, count=3):
        return [{"stdin": f"{n}\n", "expected_output": f"{n * 2}"}
                for n in range(1, count + 1)]

    def _make_question(self, title="Double it", cases=None,
                       status=Question.STATUS_PUBLISHED):
        return Question.objects.create(
            title=title, content="Double the input.", topic=self.topic,
            base_difficulty=1200.0,
            hidden_test_cases=self._cases() if cases is None else cases,
            boilerplate_code={"python": "def solve(n): ..."},
            hidden_wrapper_code={}, status=status)

    def _record_evidence(self, question=None, reference=None, runs=2,
                         output_for=None, status=None):
        """Write real provenance rows for every case."""
        question = question or self.question
        reference = reference or self.reference
        output_for = output_for or (lambda case: case["expected_output"])
        for case in question.hidden_test_cases:
            for _ in range(runs):
                provenance.record_execution(
                    question=question, reference=reference,
                    stdin=case["stdin"], produced_output=output_for(case),
                    status=status or OracleExecution.STATUS_SUCCESS,
                    execution_contract_version="v1",
                    executor={"operator": self.operator.username})

    def _artifact(self, quality=None):
        return build_artifact(self.question, self.reference,
                              quality or passing_quality())

    def _quality_file(self, quality=None):
        quality = quality or passing_quality()
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        handle.write(json.dumps(quality.as_dict()))
        handle.close()
        self.addCleanup(
            lambda: pathlib.Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def _approve(self, **overrides):
        """Drive the real approve command."""
        options = {
            "question": str(self.question.pk),
            "digest": self._artifact().digest(),
            "quality_report": self._quality_file(),
            "operator": self.operator.username,
        }
        options.update(overrides)
        call_command("question_approve", "--question", options["question"],
                     "--digest", options["digest"],
                     "--quality-report", options["quality_report"],
                     "--operator", options["operator"], "--confirm")
        return QuestionApproval.current_for(self.question)


# ═════════════════════════════════════════════════════════════
# Digest specification
# ═════════════════════════════════════════════════════════════

class DigestTests(TrustTestCase):

    def test_same_artifact_yields_the_same_digest(self):
        self.assertEqual(self._artifact().digest(), self._artifact().digest())

    def test_digest_is_stable_across_language_map_insertion_order(self):
        """
        Canonical ordering, not Python's dict ordering.

        Needs SEVERAL languages to test anything: a one-key map digests
        identically whether or not the code sorts, and a mutation sweep showed
        an earlier single-key version of this test could not detect `sorted()`
        being removed.
        """
        languages = {"python": "py stub", "java": "java stub",
                     "cpp": "cpp stub", "javascript": "js stub"}
        self.question.boilerplate_code = dict(languages)
        self.question.hidden_wrapper_code = dict(languages)
        forward = self._artifact().digest()

        self.question.boilerplate_code = {
            k: languages[k] for k in sorted(languages, reverse=True)}
        self.question.hidden_wrapper_code = {
            k: languages[k] for k in sorted(languages, reverse=True)}
        self.assertEqual(self._artifact().digest(), forward)

    def test_a_language_value_change_still_changes_the_digest(self):
        """Order-insensitivity must not become value-insensitivity."""
        self.question.boilerplate_code = {"python": "a", "java": "b"}
        before = self._artifact().digest()
        self.question.boilerplate_code = {"python": "a", "java": "CHANGED"}
        self.assertNotEqual(self._artifact().digest(), before)

    def test_swapping_two_languages_sources_changes_the_digest(self):
        """
        The sharpest ordering test: same key set, same value multiset, values
        exchanged between keys. Only a key-bound encoding distinguishes these.
        """
        self.question.boilerplate_code = {"python": "A", "java": "B"}
        before = self._artifact().digest()
        self.question.boilerplate_code = {"python": "B", "java": "A"}
        self.assertNotEqual(self._artifact().digest(), before)

    def test_case_reorder_does_not_change_the_digest(self):
        """
        Grading treats the suite as a set, so a benign reorder must not
        invalidate a reviewed artifact — while any content edit still must.
        """
        before = self._artifact().digest()
        self.question.hidden_test_cases = list(
            reversed(self.question.hidden_test_cases))
        self.question.save(update_fields=["hidden_test_cases"])
        self.assertEqual(self._artifact().digest(), before)

    def test_content_change_changes_the_digest(self):
        before = self._artifact().digest()
        self.question.content += " Now with clarification."
        self.assertNotEqual(self._artifact().digest(), before)

    def test_whitespace_only_content_change_changes_the_digest(self):
        """Statement text is byte-exact: whitespace can change a problem."""
        before = self._artifact().digest()
        self.question.content += "\n"
        self.assertNotEqual(self._artifact().digest(), before)

    def test_expected_output_change_changes_the_digest(self):
        before = self._artifact().digest()
        cases = list(self.question.hidden_test_cases)
        cases[0] = {**cases[0], "expected_output": "999"}
        self.question.hidden_test_cases = cases
        self.assertNotEqual(self._artifact().digest(), before)

    def test_hidden_test_input_change_changes_the_digest(self):
        before = self._artifact().digest()
        cases = list(self.question.hidden_test_cases)
        cases[0] = {**cases[0], "stdin": "77\n"}
        self.question.hidden_test_cases = cases
        self.assertNotEqual(self._artifact().digest(), before)

    def test_adding_a_hidden_test_changes_the_digest(self):
        before = self._artifact().digest()
        self.question.hidden_test_cases = self.question.hidden_test_cases + [
            {"stdin": "50\n", "expected_output": "100"}]
        self.assertNotEqual(self._artifact().digest(), before)

    def test_reference_source_change_changes_the_digest(self):
        before = self._artifact().digest()
        self.reference.source_hash = "0" * 64
        self.assertNotEqual(self._artifact().digest(), before)

    def test_reference_source_hash_participates_in_the_digest_directly(self):
        """
        Isolated: only the hash frame differs, evidence held constant.

        The test above changes the hash on the live reference, which ALSO
        changes which provenance rows resolve — so it passes even if the hash
        never reaches the encoding. A mutation sweep proved that: deleting the
        `reference_source_hash` frame survived. This pins the frame itself.
        """
        artifact = self._artifact()
        before = artifact.digest()
        artifact.reference_source_hash = "0" * 64
        self.assertNotEqual(artifact.digest(), before)

    def test_reference_identity_participates_in_the_digest_directly(self):
        artifact = self._artifact()
        before = artifact.digest()
        artifact.reference_id = artifact.reference_id + 1
        self.assertNotEqual(artifact.digest(), before)

    def test_execution_contract_change_changes_the_digest(self):
        before = self._artifact().digest()
        self.question.execution_contract_version = "v2"
        self.assertNotEqual(self._artifact().digest(), before)

    def test_boilerplate_change_changes_the_digest(self):
        before = self._artifact().digest()
        self.question.boilerplate_code = {"python": "different stub"}
        self.assertNotEqual(self._artifact().digest(), before)

    def test_wrapper_change_changes_the_digest(self):
        before = self._artifact().digest()
        self.question.hidden_wrapper_code = {"python": "wrapper"}
        self.assertNotEqual(self._artifact().digest(), before)

    def test_quality_result_change_changes_the_digest(self):
        before = self._artifact().digest()
        other = QualityOutcome(tier1_kill_rate=1.0, tier2_kill_rate=0.81,
                               blockers=(), mutant_identifiers=("m1", "m2", "m3"))
        self.assertNotEqual(self._artifact(other).digest(), before)

    def test_mutant_set_change_changes_the_digest(self):
        """
        Three easy mutants and forty hard ones both score 1.00. Without the
        mutant-set digest they would produce identical artifacts.
        """
        before = self._artifact().digest()
        wider = passing_quality(mutants=("m1", "m2", "m3", "m4"))
        self.assertNotEqual(self._artifact(wider).digest(), before)

    def test_schema_version_change_changes_the_digest(self):
        artifact = self._artifact()
        before = artifact.digest()
        artifact.schema_version = ARTIFACT_SCHEMA_VERSION + 1
        self.assertNotEqual(artifact.digest(), before)

    def test_oracle_output_change_changes_the_digest(self):
        before = self._artifact().digest()
        artifact = self._artifact()
        artifact.cases = [
            CaseEvidence(c.case_digest, c.input_digest, c.expected_digest,
                         "f" * 64, c.agreeing_runs) for c in artifact.cases]
        self.assertNotEqual(artifact.digest(), before)

    def test_agreeing_run_count_change_changes_the_digest(self):
        before = self._artifact().digest()
        artifact = self._artifact()
        artifact.cases = [
            CaseEvidence(c.case_digest, c.input_digest, c.expected_digest,
                         c.oracle_output_digest, c.agreeing_runs + 1)
            for c in artifact.cases]
        self.assertNotEqual(artifact.digest(), before)

    def test_length_prefixing_prevents_field_forgery(self):
        """
        A value containing the frame separators must not be readable as extra
        fields. Two artifacts differing only in where a boundary would fall
        must still digest differently.
        """
        first = self._artifact()
        first.content = "a|5:aaaaa"
        second = self._artifact()
        second.content = "a|5:aaaa"
        self.assertNotEqual(first.digest(), second.digest())


# ═════════════════════════════════════════════════════════════
# Oracle evidence and legacy data
# ═════════════════════════════════════════════════════════════

class EvidenceTests(TrustTestCase):

    def test_full_evidence_is_oracle_backed(self):
        artifact = self._artifact()
        self.assertTrue(artifact.is_fully_oracle_backed)
        self.assertEqual(artifact.blockers, [])

    def test_question_with_no_executions_is_not_approvable(self):
        """
        THE legacy case. Values that look right are not evidence that they are
        right; without provenance there is nothing to promote.
        """
        OracleExecution.objects.all().delete()
        artifact = self._artifact()
        self.assertFalse(artifact.is_fully_oracle_backed)
        self.assertTrue(any("no successful oracle execution" in b
                            for b in artifact.blockers), artifact.blockers)

    def test_expected_output_matching_by_coincidence_is_still_not_backed(self):
        """
        A legacy answer that happens to equal a value the oracle produced for
        a DIFFERENT question earns nothing. Evidence is scoped to this
        question and this reference revision.
        """
        other = self._make_question(title="Unrelated")
        other_reference = approved_reference(other, source_code=SOURCE)
        other_reference.refresh_from_db()
        OracleExecution.objects.filter(question=self.question).delete()
        self._record_evidence(question=other, reference=other_reference)

        artifact = self._artifact()
        self.assertFalse(artifact.is_fully_oracle_backed)

    def test_evidence_from_a_superseded_reference_revision_does_not_count(self):
        artifact_before = self._artifact()
        self.assertTrue(artifact_before.is_fully_oracle_backed)

        # Same reference object, different source revision.
        self.reference.source_hash = "1" * 64
        artifact = build_artifact(self.question, self.reference,
                                  passing_quality())
        self.assertFalse(artifact.is_fully_oracle_backed)

    def test_a_single_run_is_not_determinism_evidence(self):
        OracleExecution.objects.all().delete()
        self._record_evidence(runs=1)
        artifact = self._artifact()
        self.assertTrue(any("agreeing run" in b for b in artifact.blockers),
                        artifact.blockers)

    def test_nondeterministic_execution_on_record_blocks(self):
        self._record_evidence(
            status=OracleExecution.STATUS_NONDETERMINISTIC)
        artifact = self._artifact()
        self.assertTrue(any("nondeterministic" in b for b in artifact.blockers),
                        artifact.blockers)

    def test_conflicting_successful_outputs_block(self):
        self._record_evidence(runs=1, output_for=lambda case: "different")
        artifact = self._artifact()
        self.assertTrue(any("conflicting" in b for b in artifact.blockers),
                        artifact.blockers)

    def test_stored_answer_disagreeing_with_oracle_is_not_backed(self):
        OracleExecution.objects.all().delete()
        self._record_evidence(output_for=lambda case: "WRONG")
        artifact = self._artifact()
        self.assertFalse(artifact.is_fully_oracle_backed)
        self.assertTrue(any("not backed by" in b for b in artifact.blockers),
                        artifact.blockers)

    def test_failing_quality_gate_blocks(self):
        artifact = self._artifact(failing_quality())
        self.assertTrue(any("quality gate" in b for b in artifact.blockers),
                        artifact.blockers)


# ═════════════════════════════════════════════════════════════
# question_review — read-only
# ═════════════════════════════════════════════════════════════

class ReviewCommandTests(TrustTestCase):

    def _review(self, operator=None):
        call_command("question_review", "--question", str(self.question.pk),
                     "--operator", operator or self.operator.username,
                     "--quality-report", self._quality_file())

    def test_review_refuses_a_non_staff_operator(self):
        with self.assertRaises(CommandError) as caught:
            self._review(operator=self.learner.username)
        self.assertIn("not staff", str(caught.exception))

    def test_review_refuses_an_inactive_operator(self):
        self.operator.is_active = False
        self.operator.save(update_fields=["is_active"])
        with self.assertRaises(CommandError) as caught:
            self._review()
        self.assertIn("not an active account", str(caught.exception))

    def test_review_writes_nothing(self):
        before = (self.question.content, self.question.hidden_test_cases,
                  self.question.status, self.question.trust_state)
        approvals = QuestionApproval.objects.count()
        executions = OracleExecution.objects.count()

        self._review()

        self.question.refresh_from_db()
        self.assertEqual(
            (self.question.content, self.question.hidden_test_cases,
             self.question.status, self.question.trust_state), before)
        self.assertEqual(QuestionApproval.objects.count(), approvals)
        self.assertEqual(OracleExecution.objects.count(), executions)

    def test_review_has_no_write_flags(self):
        from groups.management.commands.question_review import Command
        parser = Command().create_parser("manage.py", "question_review")
        flags = {action.dest for action in parser._actions}
        for forbidden in ("approve", "confirm", "apply", "promote", "force"):
            self.assertNotIn(forbidden, flags)


# ═════════════════════════════════════════════════════════════
# question_approve
# ═════════════════════════════════════════════════════════════

class ApproveCommandTests(TrustTestCase):

    def test_approval_records_the_digest_and_actors(self):
        approval = self._approve()
        self.assertEqual(approval.artifact_digest, self._artifact().digest())
        self.assertEqual(approval.approved_by, self.operator)
        self.assertEqual(approval.reference, self.reference)
        self.assertEqual(approval.reference_source_hash,
                         self.reference.source_hash)
        self.assertEqual(approval.artifact_schema_version,
                         ARTIFACT_SCHEMA_VERSION)
        self.assertIsNotNone(approval.approved_at)
        self.assertIsNone(approval.promoted_at)

    def test_approval_records_who_executed_the_oracle(self):
        """B5: no four-eyes rule, but the actors are separately recorded so
        one can be added later as a constraint rather than a migration."""
        approval = self._approve()
        self.assertEqual(approval.executed_by, self.operator)
        self.assertIsNotNone(approval.executed_at)
        self.assertIsNotNone(approval.reviewed_at)

    def test_approval_does_not_change_trust_state(self):
        self._approve()
        self.question.refresh_from_db()
        self.assertEqual(self.question.trust_state, Question.TRUST_UNVERIFIED)

    def test_approval_does_not_publish_or_activate_anything(self):
        status_before = self.question.status
        self._approve()
        self.question.refresh_from_db()
        self.reference.refresh_from_db()
        self.assertEqual(self.question.status, status_before)
        self.assertEqual(self.reference.review_state, "APPROVED")

    def test_approval_does_not_touch_grading_data(self):
        cases_before = list(self.question.hidden_test_cases)
        content_before = self.question.content
        self._approve()
        self.question.refresh_from_db()
        self.assertEqual(self.question.hidden_test_cases, cases_before)
        self.assertEqual(self.question.content, content_before)

    def test_approve_requires_confirm(self):
        with self.assertRaises(CommandError) as caught:
            call_command("question_approve", "--question",
                         str(self.question.pk), "--digest",
                         self._artifact().digest(), "--quality-report",
                         self._quality_file(), "--operator",
                         self.operator.username)
        self.assertIn("--confirm", str(caught.exception))

    def test_approve_refuses_a_non_staff_operator(self):
        with self.assertRaises(CommandError) as caught:
            self._approve(operator=self.learner.username)
        self.assertIn("not staff", str(caught.exception))

    def test_approve_refuses_a_mismatched_digest(self):
        with self.assertRaises(CommandError) as caught:
            self._approve(digest="a" * 64)
        self.assertIn("digest mismatch", str(caught.exception))
        self.assertEqual(QuestionApproval.objects.count(), 0)

    def test_approve_refuses_a_digest_that_went_stale_between_review_and_approve(self):
        """The artifact moved after the reviewer read it."""
        stale = self._artifact().digest()
        self.question.content += " edited after review"
        self.question.save(update_fields=["content"])
        with self.assertRaises(CommandError) as caught:
            self._approve(digest=stale)
        self.assertIn("digest mismatch", str(caught.exception))

    def test_approve_refuses_without_oracle_evidence(self):
        OracleExecution.objects.all().delete()
        with self.assertRaises(CommandError):
            self._approve()
        self.assertEqual(QuestionApproval.objects.count(), 0)

    def test_approve_refuses_a_failing_quality_gate(self):
        quality = failing_quality()
        with self.assertRaises(CommandError) as caught:
            self._approve(digest=self._artifact(quality).digest(),
                          quality_report=self._quality_file(quality))
        self.assertIn("quality gate", str(caught.exception))

    def test_approve_refuses_without_a_canonical_reference(self):
        self.reference.deactivate()
        with self.assertRaises(CommandError) as caught:
            self._approve()
        self.assertIn("no canonical reference", str(caught.exception))

    def test_approve_refuses_a_nondeterministic_artifact(self):
        self._record_evidence(status=OracleExecution.STATUS_NONDETERMINISTIC)
        with self.assertRaises(CommandError):
            self._approve(digest=self._artifact().digest())

    def test_approve_refuses_a_conflicting_expected_output(self):
        OracleExecution.objects.all().delete()
        self._record_evidence(output_for=lambda case: "WRONG")
        with self.assertRaises(CommandError):
            self._approve(digest=self._artifact().digest())


# ═════════════════════════════════════════════════════════════
# question_promote
# ═════════════════════════════════════════════════════════════

class PromoteCommandTests(TrustTestCase):

    def _promote(self, operator=None, confirm=True):
        args = ["question_promote", "--question", str(self.question.pk),
                "--operator", operator or self.operator.username]
        if confirm:
            args.append("--confirm")
        call_command(*args)

    def test_promotion_after_approval_sets_oracle_verified(self):
        approval = self._approve()
        self._promote()
        self.question.refresh_from_db()
        approval.refresh_from_db()
        self.assertEqual(self.question.trust_state,
                         Question.TRUST_ORACLE_VERIFIED)
        self.assertIsNotNone(approval.promoted_at)
        self.assertEqual(approval.promoted_by, self.operator)

    def test_promotion_without_approval_is_refused(self):
        """
        Oracle evidence, determinism and a passing quality gate are all
        present. None of it promotes anything.
        """
        with self.assertRaises(CommandError) as caught:
            self._promote()
        self.assertIn("no approval", str(caught.exception))
        self.question.refresh_from_db()
        self.assertEqual(self.question.trust_state, Question.TRUST_UNVERIFIED)

    def test_promotion_requires_confirm(self):
        self._approve()
        with self.assertRaises(CommandError):
            self._promote(confirm=False)
        self.question.refresh_from_db()
        self.assertEqual(self.question.trust_state, Question.TRUST_UNVERIFIED)

    def test_promotion_refuses_a_non_staff_operator(self):
        self._approve()
        with self.assertRaises(CommandError) as caught:
            self._promote(operator=self.learner.username)
        self.assertIn("not staff", str(caught.exception))

    def test_promotion_refuses_a_stale_approval_after_content_change(self):
        self._approve()
        self.question.content += " edited after approval"
        self.question.save(update_fields=["content"])
        with self.assertRaises(CommandError) as caught:
            self._promote()
        self.assertIn("STALE APPROVAL", str(caught.exception))
        self.question.refresh_from_db()
        self.assertEqual(self.question.trust_state, Question.TRUST_UNVERIFIED)

    def test_promotion_refuses_a_stale_approval_after_hidden_test_change(self):
        self._approve()
        cases = list(self.question.hidden_test_cases)
        cases[0] = {**cases[0], "expected_output": "999"}
        self.question.hidden_test_cases = cases
        self.question.save(update_fields=["hidden_test_cases"])
        with self.assertRaises(CommandError):
            self._promote()
        self.question.refresh_from_db()
        self.assertEqual(self.question.trust_state, Question.TRUST_UNVERIFIED)

    def test_promotion_refuses_when_the_reference_is_deactivated(self):
        self._approve()
        self.reference.deactivate()
        with self.assertRaises(CommandError) as caught:
            self._promote()
        self.assertIn("no canonical reference", str(caught.exception))

    def test_promotion_refuses_when_a_different_reference_became_canonical(self):
        self._approve()
        self.reference.deactivate()
        replacement = approved_reference(
            self.question, language="java", source_code="class X {}")
        replacement.refresh_from_db()
        with self.assertRaises(CommandError) as caught:
            self._promote()
        self.assertIn("was granted against reference", str(caught.exception))

    def test_promotion_refuses_from_draft(self):
        self.question.status = Question.STATUS_DRAFT
        self.question.save(update_fields=["status"])
        self._approve()
        with self.assertRaises(CommandError) as caught:
            self._promote()
        self.assertIn("DRAFT", str(caught.exception))

    def test_promotion_refuses_when_evidence_is_deleted_after_approval(self):
        self._approve()
        OracleExecution.objects.all().delete()
        with self.assertRaises(CommandError):
            self._promote()
        self.question.refresh_from_db()
        self.assertEqual(self.question.trust_state, Question.TRUST_UNVERIFIED)

    def test_promotion_refuses_a_stale_artifact_schema(self):
        approval = self._approve()
        QuestionApproval.objects.filter(pk=approval.pk).update(
            artifact_schema_version=ARTIFACT_SCHEMA_VERSION + 1)
        with self.assertRaises(CommandError) as caught:
            self._promote()
        self.assertIn("schema", str(caught.exception))

    def test_promotion_refuses_an_approval_holding_a_failing_quality_verdict(self):
        """
        Quality is guarded twice: `build_artifact` records a blocker, and
        promotion checks the frozen verdict directly before building anything.

        The message is asserted, not just the refusal. Both guards produce a
        CommandError, so asserting only "it raised" cannot tell them apart —
        a mutation sweep showed that deleting promotion's own check survived a
        weaker version of this test. Pinning the wording pins WHICH guard
        fired, which is the difference between a defence in depth and a
        defence that quietly became decorative.
        """
        approval = self._approve()
        QuestionApproval.objects.filter(pk=approval.pk).update(
            quality_outcome=failing_quality().as_dict())

        with self.assertRaises(CommandError) as caught:
            self._promote()
        self.assertIn("froze a FAILING quality verdict", str(caught.exception))

        self.question.refresh_from_db()
        self.assertEqual(self.question.trust_state, Question.TRUST_UNVERIFIED)

    def test_promotion_does_not_activate_the_reference(self):
        self._approve()
        self.reference.refresh_from_db()
        active_before = self.reference.is_active
        self._promote()
        self.reference.refresh_from_db()
        self.assertEqual(self.reference.is_active, active_before)

    def test_promotion_does_not_publish_or_alter_grading_data(self):
        status_before = self.question.status
        cases_before = list(self.question.hidden_test_cases)
        content_before = self.question.content
        self._approve()
        self._promote()
        self.question.refresh_from_db()
        self.assertEqual(self.question.status, status_before)
        self.assertEqual(self.question.hidden_test_cases, cases_before)
        self.assertEqual(self.question.content, content_before)

    def test_promote_accepts_no_caller_supplied_trust_value(self):
        from groups.management.commands.question_promote import Command
        parser = Command().create_parser("manage.py", "question_promote")
        flags = {action.dest for action in parser._actions}
        for forbidden in ("trust_state", "force", "skip_quality",
                          "skip_approval", "digest"):
            self.assertNotIn(forbidden, flags)


# ═════════════════════════════════════════════════════════════
# Model-level guarantees
# ═════════════════════════════════════════════════════════════

class ApprovalModelTests(TrustTestCase):

    def test_approval_is_append_only(self):
        approval = self._approve()
        approval.artifact_digest = "b" * 64
        with self.assertRaises(ValidationError):
            approval.save()

    def test_only_the_promotion_stamp_may_be_added(self):
        approval = self._approve()
        approval.promoted_at = approval.approved_at
        approval.save(update_fields=["promoted_at"])   # must not raise

    def test_approval_may_not_cross_questions(self):
        other = self._make_question(title="Other")
        approval = QuestionApproval(
            question=other, reference=self.reference,
            reference_source_hash=self.reference.source_hash,
            artifact_digest="c" * 64, approved_by=self.operator,
            approved_at=self.question.__class__.objects.first().pk and None)
        with self.assertRaises(ValidationError):
            approval.clean()

    def test_draft_plus_oracle_verified_is_rejected_by_the_database(self):
        self.question.status = Question.STATUS_DRAFT
        self.question.trust_state = Question.TRUST_ORACLE_VERIFIED
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.question.save(update_fields=["status", "trust_state"])

    def test_migration_preflight_refuses_when_offending_rows_exist(self):
        """
        The fail-safe, exercised.

        The state it guards against is unwritable once the constraint exists,
        so it cannot be reproduced by inserting a row — which is precisely why
        it needs a direct test. Production has never been surveyed (P2.7e), so
        this branch may genuinely fire there, and an untested failure path is
        not a safeguard.
        """
        _0042 = importlib.import_module(
            "groups.migrations.0042_question_approval")

        class FakeQuerySet:
            def count(self):
                return 2

            def values_list(self, *args, **kwargs):
                return [7, 9]

        class FakeManager:
            def filter(self, **kwargs):
                assert kwargs == {"status": "DRAFT",
                                  "trust_state": "ORACLE_VERIFIED"}, kwargs
                return FakeQuerySet()

        class FakeModel:
            objects = FakeManager()

        class FakeApps:
            def get_model(self, app, model):
                return FakeModel

        with self.assertRaises(RuntimeError) as caught:
            _0042.assert_no_draft_oracle_verified(FakeApps(), None)

        message = str(caught.exception)
        self.assertIn("2 question(s)", message)
        self.assertIn("[7, 9]", message)
        self.assertIn("Nothing has been modified", message)

    def test_migration_preflight_passes_on_a_clean_database(self):
        _0042 = importlib.import_module(
            "groups.migrations.0042_question_approval")

        class FakeQuerySet:
            def count(self):
                return 0

        class FakeModel:
            class objects:
                @staticmethod
                def filter(**kwargs):
                    return FakeQuerySet()

        class FakeApps:
            def get_model(self, app, model):
                return FakeModel

        _0042.assert_no_draft_oracle_verified(FakeApps(), None)   # must not raise

    def test_migration_contains_no_data_rewrite(self):
        """
        Additive only: one read-only RunPython, one CreateModel, one
        AddConstraint. No backfill, no promotion, no rewrite.

        AST-based, not a text scan. A first version searched for the string
        `trust_state=` and matched the pre-flight's own QUERY FILTER —
        `filter(status="DRAFT", trust_state="ORACLE_VERIFIED")` — which is how
        the check READS the rows it refuses to touch. A guard that cannot tell
        a filter from an assignment would forbid the migration from looking at
        the thing it exists to look at.
        """
        path = (pathlib.Path(inspect.getfile(question_artifact)).parent
                / "migrations" / "0042_question_approval.py")
        assigned, called = _write_targets(path)

        self.assertEqual(
            assigned & (GRADING_TRUTH | {"trust_state", "status"}), set(),
            "migration assigns to grading truth or trust state")
        self.assertEqual(called, set(),
                         "migration performs a database write")

        source = path.read_text(encoding="utf-8")
        self.assertNotIn("RunSQL", source,
                         "migration executes raw SQL; review it by hand")

    def test_models_and_migrations_have_not_drifted(self):
        """
        A constraint declared on the model but absent from a migration is a
        constraint that does not exist in any database. Django can detect the
        gap; nothing else in this suite would, because the test database is
        built from migrations and would simply behave as the migration says.
        """
        from io import StringIO
        from django.core.management import call_command as run

        buffer = StringIO()
        try:
            run("makemigrations", "groups", "--check", "--dry-run",
                stdout=buffer, stderr=buffer)
        except SystemExit as exc:                       # --check exits 1
            self.fail(f"unapplied model changes detected:\n{buffer.getvalue()}"
                      if exc.code else "")

    def test_published_plus_unverified_remains_legal(self):
        self.question.status = Question.STATUS_PUBLISHED
        self.question.trust_state = Question.TRUST_UNVERIFIED
        self.question.save(update_fields=["status", "trust_state"])

    def test_blocked_plus_oracle_verified_remains_legal(self):
        """A proven answer key withdrawn for an unrelated reason."""
        self.question.status = Question.STATUS_BLOCKED
        self.question.trust_state = Question.TRUST_ORACLE_VERIFIED
        self.question.save(update_fields=["status", "trust_state"])


# ═════════════════════════════════════════════════════════════
# Admin hardening
# ═════════════════════════════════════════════════════════════

class AdminHardeningTests(TestCase):

    def test_grading_artifact_fields_are_readonly(self):
        from django.contrib.admin.sites import AdminSite
        from groups.admin import QuestionAdmin

        admin = QuestionAdmin(Question, AdminSite())
        readonly = set(admin.get_readonly_fields(request=None))
        for locked in ("status", "trust_state", "content", "hidden_test_cases",
                       "boilerplate_code", "hidden_wrapper_code",
                       "execution_contract_version"):
            self.assertIn(locked, readonly)

    def test_admin_form_cannot_mutate_the_grading_artifact(self):
        """
        The regression that matters: not "the list says readonly" but "the
        generated form has no field to submit". Readonly fields are excluded
        from the form entirely, so a crafted POST has nothing to bind to.
        """
        from django.contrib.admin.sites import AdminSite
        from groups.admin import QuestionAdmin

        admin = QuestionAdmin(Question, AdminSite())
        form_class = admin.get_form(request=None, obj=None, change=False)
        fields = set(form_class.base_fields)
        for locked in ("status", "trust_state", "content", "hidden_test_cases",
                       "boilerplate_code", "hidden_wrapper_code",
                       "execution_contract_version"):
            self.assertNotIn(
                locked, fields,
                f"{locked} is submittable through the admin form")

    def test_reference_and_provenance_are_not_registered_in_admin(self):
        from django.contrib import admin as django_admin
        from groups.models import OracleExecution, ReferenceSolution

        registered = set(django_admin.site._registry)
        self.assertNotIn(ReferenceSolution, registered)
        self.assertNotIn(OracleExecution, registered)
        self.assertNotIn(QuestionApproval, registered)


# ═════════════════════════════════════════════════════════════
# Learner-facing surface
# ═════════════════════════════════════════════════════════════

class LearnerSurfaceTests(TestCase):

    def test_no_serializer_exposes_question_trust_fields(self):
        """
        Stronger than a permission check: there is no Question ModelSerializer
        at all, so there is no field for a learner to inject into.
        """
        from rest_framework import serializers as drf
        from groups import serializers as module

        for name in dir(module):
            candidate = getattr(module, name)
            if not (isinstance(candidate, type)
                    and issubclass(candidate, drf.ModelSerializer)):
                continue
            meta = getattr(candidate, "Meta", None)
            if getattr(meta, "model", None) is not Question:
                continue
            self.fail(f"{name} exposes Question through a ModelSerializer; "
                      f"trust fields could be injected")

    def test_no_url_route_mentions_promotion_or_approval(self):
        from groups import urls

        source = pathlib.Path(inspect.getfile(urls)).read_text(encoding="utf-8")
        for forbidden in ("question_promote", "question_approve",
                          "trust_state", "promote"):
            self.assertNotIn(forbidden, source)


# ═════════════════════════════════════════════════════════════
# Structural write boundary
# ═════════════════════════════════════════════════════════════

def _write_targets(path):
    """
    Names assigned to, and persistence calls made, INSIDE FUNCTION BODIES.

    AST-based, because a docstring promising not to write is prose and three
    earlier phases had a text-matching guard defeated by exactly that.

    Scoped to function bodies for a reason this guard learned the hard way: a
    first version flagged `groups/models.py` as a `trust_state` writer and
    `question_artifact.py` as a `content` writer. Both were DECLARATIONS — a
    model field and a dataclass field that happen to share the name of the
    thing being protected. Declaring a field named `content` is not writing a
    question's content, and a guard that cannot tell the difference would
    force every future model to avoid the vocabulary of its own domain.
    Assignments that mutate state live in functions; class and module bodies
    declare.
    """
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    assigned, called = set(), set()

    def names(node):
        if isinstance(node, ast.Attribute):
            return [node.attr]
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return [key.value]
            return []
        if isinstance(node, (ast.Tuple, ast.List)):
            return [n for e in node.elts for n in names(e)]
        return []

    def visit(node, in_function):
        entering = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        inside = in_function or entering

        if inside:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                assigned.update(names(target))

            if isinstance(node, ast.Call):
                persistence = _persistence_call(node.func)
                if persistence:
                    called.add(persistence)

        for child in ast.iter_child_nodes(node):
            visit(child, inside)

    visit(tree, False)
    return assigned, called


#: Mutators reached through a manager, e.g. `Question.objects.update(...)`.
MANAGER_MUTATORS = {"create", "update", "bulk_update", "bulk_create",
                    "delete", "get_or_create", "update_or_create"}


def _persistence_call(function):
    """
    The persistence operation a call performs, or None.

    RECEIVER-AWARE, and it has to be: a first version matched on method name
    alone and flagged `hasher.update(frame)` — a sha256 call — as a database
    write. Matching `update` anywhere would either force this module to avoid
    hashlib's API or force the guard to be switched off, and a guard that gets
    switched off protects nothing.
    """
    if not isinstance(function, ast.Attribute):
        return None

    # `x.save(...)` / `x.delete(...)` — unambiguous on any receiver.
    if function.attr in {"save", "delete"}:
        return function.attr

    # `X.objects.<mutator>(...)` — the manager is what makes it persistence.
    if function.attr in MANAGER_MUTATORS:
        receiver = function.value
        if isinstance(receiver, ast.Attribute) and receiver.attr == "objects":
            return f"objects.{function.attr}"

    return None


GRADING_TRUTH = {"expected_output", "hidden_test_cases", "content",
                 "boilerplate_code", "hidden_wrapper_code",
                 "execution_contract_version"}
#: Anything `_persistence_call` recognises. Compared as a non-empty set: the
#: assertion is "this module performs no persistence at all", so the exact
#: labels matter less than there being none.
PERSISTENCE = None


class WriteBoundaryTests(TestCase):

    def test_artifact_module_never_writes(self):
        assigned, called = _write_targets(inspect.getfile(question_artifact))
        self.assertEqual(assigned & (GRADING_TRUTH | {"trust_state", "status"}),
                         set())
        self.assertEqual(called, set())

    def test_review_command_never_writes(self):
        from groups.management.commands import question_review
        assigned, called = _write_targets(inspect.getfile(question_review))
        self.assertEqual(assigned & (GRADING_TRUTH | {"trust_state", "status"}),
                         set())
        self.assertEqual(called, set())

    def test_approve_command_never_writes_grading_truth_or_trust(self):
        from groups.management.commands import question_approve
        assigned, called = _write_targets(inspect.getfile(question_approve))
        self.assertEqual(assigned & GRADING_TRUTH, set())
        self.assertNotIn("trust_state", assigned)
        self.assertNotIn("status", assigned)

    def test_promote_command_writes_trust_state_and_nothing_else(self):
        from groups.management.commands import question_promote
        assigned, _called = _write_targets(inspect.getfile(question_promote))
        self.assertIn("trust_state", assigned,
                      "promotion is supposed to be the trust_state writer")
        self.assertEqual(assigned & GRADING_TRUTH, set())
        self.assertNotIn("status", assigned)

    def test_promotion_never_touches_reference_lifecycle(self):
        """
        Promotion must not activate, approve or otherwise alter the reference.

        Asserted structurally rather than by before/after comparison, because
        the reference is necessarily ALREADY active when promotion succeeds —
        so a mutant that re-sets `is_active = True` writes to the database
        while changing no value, and a value comparison cannot see it. A
        mutation sweep found exactly that survivor.
        """
        from groups.management.commands import question_promote
        assigned, _ = _write_targets(inspect.getfile(question_promote))
        for forbidden in ("is_active", "review_state", "approved_by",
                          "approved_at", "source_code", "source_hash"):
            self.assertNotIn(
                forbidden, assigned,
                f"question_promote assigns to reference field {forbidden!r}")

    def test_approval_never_touches_reference_lifecycle(self):
        from groups.management.commands import question_approve
        assigned, _ = _write_targets(inspect.getfile(question_approve))
        for forbidden in ("is_active", "review_state", "source_code"):
            self.assertNotIn(forbidden, assigned)

    def _scan(self, source):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(source)
            path = handle.name
        self.addCleanup(lambda: pathlib.Path(path).unlink(missing_ok=True))
        return _write_targets(path)

    def test_guard_still_catches_a_write_inside_a_function(self):
        """
        The scoping fix must not have blinded the scanner. This is the write
        the whole guard exists to catch.
        """
        assigned, _ = self._scan(
            "def f(q):\n    q.trust_state = 'ORACLE_VERIFIED'\n")
        self.assertIn("trust_state", assigned)

    def test_guard_still_catches_a_subscript_write(self):
        assigned, _ = self._scan(
            "def f(case):\n    case['expected_output'] = '1'\n")
        self.assertIn("expected_output", assigned)

    def test_guard_ignores_a_field_declaration(self):
        """A model field named `trust_state` is a declaration, not a write."""
        assigned, _ = self._scan(
            "class M:\n    trust_state = CharField()\n    content: str = ''\n")
        self.assertNotIn("trust_state", assigned)
        self.assertNotIn("content", assigned)

    def test_guard_catches_orm_persistence_but_not_hashlib(self):
        _, called = self._scan(
            "import hashlib\n"
            "def f(q):\n"
            "    h = hashlib.sha256()\n"
            "    h.update(b'x')\n"
            "    return h\n")
        self.assertEqual(called, set(), "hashlib.update read as a DB write")

        _, called = self._scan("def f(q):\n    q.save()\n")
        self.assertIn("save", called)

        _, called = self._scan(
            "def f(Q):\n    Q.objects.update(trust_state='x')\n")
        self.assertIn("objects.update", called)

    def test_guard_is_not_fooled_by_a_docstring(self):
        assigned, called = self._scan(
            '"""Sets q.trust_state and calls q.save()."""\n'
            'def f(q):\n    return q\n')
        self.assertNotIn("trust_state", assigned)
        self.assertEqual(called, set())

    def test_promote_is_the_only_trust_state_writer_in_the_codebase(self):
        """
        The invariant the whole milestone rests on. If a second writer ever
        appears, this fails and names it.
        """
        root = pathlib.Path(inspect.getfile(question_artifact)).parent
        writers = []
        for path in root.rglob("*.py"):
            if path.name.startswith("test_") or "migrations" in path.parts:
                continue
            if path.name == "conftest.py":
                continue
            try:
                assigned, _ = _write_targets(path)
            except SyntaxError:
                continue
            if "trust_state" in assigned:
                writers.append(path.name)
        self.assertEqual(sorted(writers), ["question_promote.py"], writers)
