"""
Oracle execution + reconciliation pipeline (M2 P2.7g-2).

The tests that matter most here are the NEGATIVE ones. This phase's risk is
not "the pipeline fails to run" — it is "the pipeline runs and quietly mints
grading truth". So the suite spends most of its weight proving the pipeline
REFUSES: on an unapproved reference, an inactive one, a broken hash, a
cross-question reference, a nondeterministic result, and a conflict with a
stored answer.
"""

import ast
import inspect
import pathlib

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from groups import oracle_pipeline, provenance
from groups.oracle import JUDGE0_ACCEPTED
from groups.models import (
    CodingPortal, OracleExecution, Question, ReferenceSolution, Topic,
)

SOURCE = "print(int(input()) * 2)\n"


def verdict(stdout, status_id=JUDGE0_ACCEPTED, status="Accepted"):
    """
    A Judge0 verdict in the shape `OracleService._execute` actually reads.

    It keys off `status_id`, NOT the human-readable `status`. An early version
    of these fakes set only `status`, and every execution came back as
    OracleFailed — the fake was wrong, not the code. Building verdicts through
    one helper keeps that mistake from being made twelve times.
    """
    return {"status_id": status_id, "status": status, "stdout": stdout,
            "stderr": "", "compile_output": ""}


def runner_echoing(mapping, *, default=""):
    """Runner returning a canned stdout per stdin. Never touches Judge0."""
    def runner(source, language, stdin=""):
        return verdict(mapping.get(stdin, default))
    return runner


def runner_flapping(outputs):
    """Runner returning a different stdout on each successive call."""
    calls = {"n": 0}

    def runner(source, language, stdin=""):
        index = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        return verdict(outputs[index])
    return runner


class PipelineTestCase(TestCase):
    """Shared fixture: one question, one approved+active reference."""

    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="reviewer", email="rev@t.com", password="Pv#2026xyz",
            is_staff=True)

        portal = CodingPortal.objects.create(name="Pipeline Portal")
        self.topic = Topic.objects.create(
            name="Loops", structure_type="flat", portal=portal)
        self.question = self._make_question("Double the input.")
        self.reference = self._approved_reference(SOURCE)

    def _make_question(self, title, cases=None):
        return Question.objects.create(
            title=title, content="c", topic=self.topic, base_difficulty=1200.0,
            hidden_test_cases=self._cases() if cases is None else cases,
            boilerplate_code={"python": "x"}, hidden_wrapper_code={})

    def _cases(self, count=12, expected=None):
        return [{"stdin": f"{n}\n",
                 "expected_output": (f"{n * 2}" if expected is None
                                     else expected)}
                for n in range(1, count + 1)]

    def _approved_reference(self, source, *, question=None, activate=True):
        reference = ReferenceSolution.objects.create(
            question=question or self.question, language="python",
            source_code=source)
        reference.submit_for_review()
        reference.approve(by=self.staff)
        if activate:
            reference.activate()
        reference.refresh_from_db()
        return reference

    def _agreeing_runner(self):
        return runner_echoing({f"{n}\n": f"{n * 2}" for n in range(1, 13)})


# ═════════════════════════════════════════════════════════════
# Eligibility — the gates that must hold before anything runs
# ═════════════════════════════════════════════════════════════

class ReferenceEligibilityTests(PipelineTestCase):

    def test_approved_active_reference_is_eligible(self):
        self.assertEqual(
            oracle_pipeline.check_reference_eligible(
                self.question, self.reference), [])

    def test_unapproved_reference_is_blocked(self):
        draft = ReferenceSolution.objects.create(
            question=self.question, language="java", source_code=SOURCE)
        blockers = oracle_pipeline.check_reference_eligible(self.question, draft)
        self.assertTrue(any("not APPROVED" in b for b in blockers), blockers)

    def test_approved_but_inactive_reference_is_blocked(self):
        """
        The distinction the whole phase rests on. APPROVED means 'a human read
        this'; ACTIVE means 'this is the canonical answer'. A superseded
        implementation is still approved and must not define answers.
        """
        self.reference.deactivate()
        self.reference.refresh_from_db()
        blockers = oracle_pipeline.check_reference_eligible(
            self.question, self.reference)
        self.assertTrue(any("not active" in b for b in blockers), blockers)

    def test_cross_question_reference_is_blocked(self):
        other = self._make_question("Different problem.")
        blockers = oracle_pipeline.check_reference_eligible(other, self.reference)
        self.assertTrue(any("belongs to question" in b for b in blockers), blockers)

    def test_broken_approval_provenance_is_blocked(self):
        """
        Source changed after approval => the approval no longer describes what
        would run.

        Mutated in memory and never saved, because the P2.7d CHECK constraint
        `reference_approved_source_unmodified` makes this state unwritable — so
        the guard is defence in depth against a path that bypasses the ORM. A
        test that saved would only prove the constraint fires, which
        test_reference_lifecycle already covers.
        """
        self.reference.source_code = SOURCE + "# tampered\n"
        blockers = oracle_pipeline.check_reference_eligible(
            self.question, self.reference)
        self.assertTrue(any("provenance is broken" in b for b in blockers),
                        blockers)

    def test_unknown_execution_contract_is_blocked(self):
        self.question.execution_contract_version = "v99"
        blockers = oracle_pipeline.check_reference_eligible(
            self.question, self.reference)
        self.assertTrue(
            any("declares execution contract" in b for b in blockers), blockers)


class QuestionEligibilityTests(PipelineTestCase):

    def test_well_formed_question_is_eligible(self):
        self.assertEqual(
            oracle_pipeline.check_question_eligible(self.question), [])

    def test_missing_expected_output_key_blocks_the_question(self):
        """
        A case that OMITS the key is malformed, and blocks. This is distinct
        from a present-but-empty value, which is the legitimate 'awaiting
        generation' shape — see the ABSENT test below. The pipeline must tell
        the two apart, because one is a broken record and the other is work
        not yet done.
        """
        cases = self._cases()
        del cases[0]["expected_output"]
        self.question.hidden_test_cases = cases
        blockers = oracle_pipeline.check_question_eligible(self.question)
        self.assertTrue(any("violate the contract" in b for b in blockers),
                        blockers)

    def test_normalized_duplicate_inputs_block_the_question(self):
        """
        Duplicates are caught under NORMALIZED comparison, which is stricter
        than `validate_suite`'s raw comparison: '5\\n' and '5' are the same
        case to the executor even though they differ as strings.
        """
        cases = self._cases()
        cases[1]["stdin"] = "1"          # same executed input as cases[0]
        cases[1]["expected_output"] = "2"
        self.question.hidden_test_cases = cases
        blockers = oracle_pipeline.check_question_eligible(self.question)
        self.assertTrue(any("duplicate" in b for b in blockers), blockers)

    def test_thin_suite_is_advisory_not_blocking(self):
        """Under-coverage is the quality gate's call, not this pipeline's."""
        self.question.hidden_test_cases = self._cases(count=3)
        blockers = oracle_pipeline.check_question_eligible(self.question)
        self.assertTrue(any("advisory" in b for b in blockers), blockers)
        self.assertEqual(oracle_pipeline._blocking(blockers), [])


# ═════════════════════════════════════════════════════════════
# Execution + determinism
# ═════════════════════════════════════════════════════════════

class ExecutionTests(PipelineTestCase):

    def test_agreeing_run_reports_agreement_for_every_case(self):
        report = oracle_pipeline.run_question(
            self.question, self._agreeing_runner())
        self.assertTrue(report.eligible)
        self.assertEqual(report.agreements, 12)
        self.assertEqual(report.conflicts, 0)
        self.assertEqual(report.failed_cases, 0)
        self.assertTrue(report.ready_for_quality_gate)

    def test_every_case_is_executed_twice(self):
        calls = []

        def counting(source, language, stdin=""):
            calls.append(stdin)
            return verdict("2")

        self.question.hidden_test_cases = self._cases(count=12)
        oracle_pipeline.run_question(self.question, counting)
        self.assertEqual(len(calls), 12 * oracle_pipeline.REQUIRED_RUNS)

    def test_disagreeing_runs_are_nondeterministic_and_never_reconciled(self):
        """
        The result must be discarded, not resolved. No majority vote, no
        first-wins: if the output is not a function of the input, no single
        stored value is correct.
        """
        report = oracle_pipeline.run_question(
            self.question, runner_flapping(["2", "999"]))
        first = report.cases[0]
        self.assertEqual(first.outcome, oracle_pipeline.CASE_NONDETERMINISTIC)
        self.assertIsNone(first.reconciliation)
        self.assertIsNone(first.oracle_output)
        self.assertFalse(report.ready_for_quality_gate)

    def test_execution_failure_blocks_readiness(self):
        def failing(source, language, stdin=""):
            return verdict("", status_id=11, status="Runtime Error (NZEC)")

        report = oracle_pipeline.run_question(self.question, failing)
        self.assertEqual(report.failed_cases, 12)
        self.assertFalse(report.ready_for_quality_gate)

    def test_no_canonical_reference_stops_before_execution(self):
        self.reference.deactivate()
        calls = []

        def tripwire(source, language, stdin=""):
            calls.append(stdin)
            return verdict("")

        report = oracle_pipeline.run_question(self.question, tripwire)
        self.assertFalse(report.eligible)
        self.assertEqual(calls, [], "executed without a canonical reference")

    def test_ineligible_question_executes_nothing(self):
        cases = self._cases()
        del cases[0]["expected_output"]
        self.question.hidden_test_cases = cases
        self.question.save(update_fields=["hidden_test_cases"])

        calls = []

        def tripwire(source, language, stdin=""):
            calls.append(stdin)
            return verdict("2")

        report = oracle_pipeline.run_question(self.question, tripwire)
        self.assertFalse(report.eligible)
        self.assertEqual(calls, [], "executed against a malformed suite")


# ═════════════════════════════════════════════════════════════
# Reconciliation
# ═════════════════════════════════════════════════════════════

class ReconciliationTests(PipelineTestCase):

    def test_conflict_is_reported_and_nothing_is_overwritten(self):
        stored = list(self.question.hidden_test_cases)
        report = oracle_pipeline.run_question(
            self.question, runner_echoing({}, default="WRONG"))

        self.assertEqual(report.conflicts, 12)
        self.assertFalse(report.ready_for_quality_gate)
        self.question.refresh_from_db()
        self.assertEqual(self.question.hidden_test_cases, stored,
                         "reconciliation modified the stored test cases")

    def test_absent_expected_output_is_absent_not_conflict(self):
        self.question.hidden_test_cases = self._cases(expected="")
        report = oracle_pipeline.run_question(
            self.question, self._agreeing_runner())
        self.assertEqual(report.absent, 12)
        self.assertEqual(report.conflicts, 0)

    def test_absent_cases_do_not_get_written(self):
        """
        The heart of the phase. The oracle has an answer, the question has
        none, and the gap stays open: this phase produces evidence, not truth.
        """
        self.question.hidden_test_cases = self._cases(expected="")
        self.question.save(update_fields=["hidden_test_cases"])

        oracle_pipeline.run_question(self.question, self._agreeing_runner())

        self.question.refresh_from_db()
        self.assertTrue(
            all(case["expected_output"] == ""
                for case in self.question.hidden_test_cases),
            "the pipeline generated expected_output")

    def test_reconciliation_uses_the_graders_comparator(self):
        """
        A STORED answer differing only in trailing whitespace must agree,
        exactly as it would in grading.

        The whitespace has to be on the stored side to test anything.
        `OracleService._execute` already returns `normalize_output(stdout)`, so
        the oracle's half is normalized upstream and padding it proves nothing
        — an earlier version of this test padded that side and a mutation
        sweep showed it could not detect the comparator being replaced with
        `==` on raw strings.
        """
        self.question.hidden_test_cases = [
            {"stdin": f"{n}\n", "expected_output": f"  {n * 2}  \n\n"}
            for n in range(1, 13)]
        report = oracle_pipeline.run_question(
            self.question, self._agreeing_runner())
        self.assertEqual(report.conflicts, 0)
        self.assertEqual(report.agreements, 12)

    def test_a_genuinely_different_stored_answer_still_conflicts(self):
        """Whitespace tolerance must not become tolerance of wrong answers."""
        self.question.hidden_test_cases = [
            {"stdin": f"{n}\n", "expected_output": f"  {n * 2 + 1}  \n"}
            for n in range(1, 13)]
        report = oracle_pipeline.run_question(
            self.question, self._agreeing_runner())
        self.assertEqual(report.conflicts, 12)


# ═════════════════════════════════════════════════════════════
# Provenance
# ═════════════════════════════════════════════════════════════

class ProvenanceTests(PipelineTestCase):

    def test_dry_run_records_nothing(self):
        oracle_pipeline.run_question(self.question, self._agreeing_runner())
        self.assertEqual(OracleExecution.objects.count(), 0)

    def test_recording_stores_every_run(self):
        oracle_pipeline.run_question(
            self.question, self._agreeing_runner(), record=True)
        self.assertEqual(OracleExecution.objects.count(),
                         12 * oracle_pipeline.REQUIRED_RUNS)

    def test_recorded_rows_are_never_authoritative(self):
        """
        Authoritativeness is a decision, and this phase makes no decisions.
        Every row lands as evidence only.
        """
        oracle_pipeline.run_question(
            self.question, self._agreeing_runner(), record=True)
        self.assertFalse(
            OracleExecution.objects.filter(is_authoritative=True).exists())

    def test_nondeterministic_result_is_recorded_as_such(self):
        """A failure to settle is evidence too — 'we tried' beats silence."""
        oracle_pipeline.run_question(
            self.question, runner_flapping(["2", "999"]), record=True)
        self.assertTrue(OracleExecution.objects.filter(
            status=OracleExecution.STATUS_NONDETERMINISTIC).exists())

    def test_provenance_pins_the_exact_reference_revision(self):
        oracle_pipeline.run_question(
            self.question, self._agreeing_runner(), record=True)
        hashes = set(OracleExecution.objects
                     .values_list("reference_source_hash", flat=True))
        self.assertEqual(hashes, {self.reference.source_hash})
        self.assertEqual(
            provenance.outputs_produced_by(self.reference).count(),
            12 * oracle_pipeline.REQUIRED_RUNS)


# ═════════════════════════════════════════════════════════════
# The write boundary, enforced structurally
# ═════════════════════════════════════════════════════════════

#: Names that must never be ASSIGNED TO in this phase's code.
#:
#: Reading them is not merely allowed but required — reconciliation compares
#: against `expected_output`, so a guard that banned the token outright could
#: never pass. The assertion is about direction: this phase may look at
#: grading truth and may not author it.
FORBIDDEN_ASSIGNMENT_TARGETS = frozenset({
    "expected_output", "hidden_test_cases", "trust_state", "adaptive_eligible",
    "status", "is_authoritative",
})

#: Persistence calls. `provenance.record_execution` is the one sanctioned
#: writer and it lives in another module, so nothing here should reach the ORM
#: write API directly.
FORBIDDEN_CALLS = frozenset({
    "save", "update", "bulk_update", "bulk_create", "delete", "create",
    "get_or_create", "update_or_create", "setattr",
})

#: Trust-promotion vocabulary. Referencing these at all in this phase would
#: mean a promotion path is being built here rather than after human review.
FORBIDDEN_NAMES = frozenset({
    "STATUS_PUBLISHED", "TRUST_ORACLE_VERIFIED", "ORACLE_VERIFIED",
})


def find_write_violations(path):
    """
    Every place `path` writes something this phase must not write.

    AST-based rather than textual. Three previous phases had a structural
    guard defeated by a module's own prose — a docstring saying 'does not
    touch expected_output' satisfied a raw text search and made the guard pass
    vacuously. Parsing sees code, and a docstring is not code.
    """
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    violations = []

    def target_names(node):
        """Names being written by an assignment target."""
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
            return [name for element in node.elts
                    for name in target_names(element)]
        return []

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]

        for target in targets:
            for name in target_names(target):
                if name in FORBIDDEN_ASSIGNMENT_TARGETS:
                    violations.append(
                        f"line {node.lineno}: assigns to {name!r}")

        if isinstance(node, ast.Call):
            function = node.func
            called = (function.attr if isinstance(function, ast.Attribute)
                      else function.id if isinstance(function, ast.Name)
                      else None)
            if called in FORBIDDEN_CALLS:
                violations.append(f"line {node.lineno}: calls {called}()")

        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            violations.append(f"line {node.lineno}: references {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            violations.append(f"line {node.lineno}: references {node.attr}")

    return violations


class WriteBoundaryTests(TestCase):

    def test_pipeline_cannot_write_grading_truth(self):
        violations = find_write_violations(inspect.getfile(oracle_pipeline))
        self.assertEqual(
            violations, [],
            "oracle_pipeline writes state this phase must not write: "
            + "; ".join(violations))

    def test_command_cannot_write_grading_truth(self):
        from groups.management.commands import oracle_execute
        violations = find_write_violations(inspect.getfile(oracle_execute))
        self.assertEqual(
            violations, [],
            "oracle_execute writes state this phase must not write: "
            + "; ".join(violations))

    def test_reading_expected_output_is_permitted(self):
        """
        The guard must not be so blunt that reconciliation is impossible.
        Reconciliation NEEDS to read the stored answer; if this test ever
        fails the guard has been over-tightened into uselessness.
        """
        source = "def f(case):\n    return case['expected_output']\n"
        self.assertEqual(self._violations_for(source), [])

    def test_guard_catches_a_subscript_write(self):
        source = "def f(case):\n    case['expected_output'] = '1'\n"
        self.assertTrue(self._violations_for(source))

    def test_guard_catches_an_attribute_write(self):
        source = "def f(q):\n    q.trust_state = 'ORACLE_VERIFIED'\n"
        self.assertTrue(self._violations_for(source))

    def test_guard_catches_a_save_call(self):
        source = "def f(q):\n    q.save()\n"
        self.assertTrue(self._violations_for(source))

    def test_guard_is_not_fooled_by_a_docstring(self):
        """
        The exact defeat that got past three earlier phases: prose describing
        the forbidden write, with no write in the code.
        """
        source = ('"""This module sets q.trust_state and calls q.save()."""\n'
                  'def f(q):\n    return q\n')
        self.assertEqual(self._violations_for(source), [])

    def _violations_for(self, source):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(source)
            path = handle.name
        self.addCleanup(lambda: pathlib.Path(path).unlink(missing_ok=True))
        return find_write_violations(path)


# ═════════════════════════════════════════════════════════════
# Command surface
# ═════════════════════════════════════════════════════════════

class CommandTests(PipelineTestCase):

    def test_execute_requires_an_operator(self):
        with self.assertRaises(CommandError) as caught:
            call_command("oracle_execute", "--question", str(self.question.pk),
                         "--execute")
        self.assertIn("--operator", str(caught.exception))

    def test_execute_rejects_a_non_staff_operator(self):
        get_user_model().objects.create_user(username="learner", password="x")
        with self.assertRaises(CommandError) as caught:
            call_command("oracle_execute", "--question", str(self.question.pk),
                         "--execute", "--operator", "learner")
        self.assertIn("not an active staff user", str(caught.exception))

    def test_execute_refuses_without_configured_resource_limits(self):
        """
        Unbounded execution has no defined semantics, so its results must not
        be recorded as evidence.

        Patches os.environ, NOT Django settings. The first version of this test
        used `self.settings(JUDGE0_CPU_TIME_LIMIT=None, ...)` — which does
        nothing, because `judge0_resource_limits()` reads `os.getenv`, not
        `django.conf.settings`. It passed only because the variables happened
        to be unset in that environment, and started failing the moment real
        limits were configured. A vacuous pass that survived until the config
        landed.
        """
        import os
        from unittest import mock

        cleared = {k: v for k, v in os.environ.items()
                   if not k.startswith("JUDGE0_")}
        with mock.patch.dict(os.environ, cleared, clear=True):
            with self.assertRaises(CommandError) as caught:
                call_command("oracle_execute", "--question",
                             str(self.question.pk), "--execute",
                             "--operator", "reviewer")
        self.assertIn("limits", str(caught.exception))

    def test_execute_passes_the_limits_gate_when_limits_are_configured(self):
        """
        The other half: with limits set, the refusal must NOT fire.

        Without this, deleting the limits check entirely would leave the test
        above passing, since it only asserts that SOMETHING raised.

        THE RUNNER IS PATCHED. A first version let the command run unpatched —
        and because this fixture has an approved, active reference, it would
        have driven `coding_views._run_on_judge0` and issued real submissions
        to the live Judge0 API from a unit test. Tests must not depend on, or
        consume, an external service.
        """
        import os
        from unittest import mock

        agreeing = self._agreeing_runner()

        with mock.patch.dict(os.environ,
                             {"JUDGE0_CPU_TIME_LIMIT": "5",
                              "JUDGE0_MEMORY_LIMIT": "256000"}), \
             mock.patch("groups.coding_views._run_on_judge0",
                        side_effect=agreeing) as runner:
            call_command("oracle_execute", "--question",
                         str(self.question.pk), "--execute",
                         "--operator", "reviewer")

        # Got past the limits gate and reached execution — which is the claim.
        self.assertTrue(runner.called)
        self.assertEqual(
            OracleExecution.objects.filter(question=self.question).count(),
            12 * oracle_pipeline.REQUIRED_RUNS)

    def test_no_test_ever_calls_the_live_http_client(self):
        """
        Structural: no test module may CALL the real HTTP client.

        Cheap insurance against the mistake above recurring — a unit test that
        silently talks to a paid external API is slow, flaky, and spends the
        operator's quota.

        AST-based, checking for actual CALLS. A first version grepped for the
        text "requests.post" and flagged two false positives: a
        `monkeypatch.setattr("...requests.post", dead_post)` that exists
        precisely to BLOCK the network, and this guard's own search string.
        Naming a dangerous call in order to prevent it is the opposite of
        making it.
        """
        import ast
        import inspect
        import pathlib

        suite = pathlib.Path(inspect.getfile(oracle_pipeline)).parent
        offenders = []
        for path in sorted(suite.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if (isinstance(function, ast.Attribute)
                        and function.attr in {"post", "get", "request"}
                        and isinstance(function.value, ast.Name)
                        and function.value.id == "requests"):
                    offenders.append(f"{path.name}:{node.lineno}")

        self.assertEqual(offenders, [], f"live HTTP calls in tests: {offenders}")

    def test_requires_a_target(self):
        with self.assertRaises(CommandError):
            call_command("oracle_execute")

    def test_rejects_unknown_question(self):
        with self.assertRaises(CommandError) as caught:
            call_command("oracle_execute", "--question", "999999")
        self.assertIn("no such question", str(caught.exception))

    def test_has_no_flag_that_writes_expected_output(self):
        """
        Not 'the flag is guarded' — the flag does not exist. A reviewer
        skimming `--help` should be unable to find a way to mint answers.
        """
        from groups.management.commands.oracle_execute import Command
        parser = Command().create_parser("manage.py", "oracle_execute")
        flags = {action.dest for action in parser._actions}
        self.assertNotIn("write", flags)
        self.assertNotIn("generate", flags)
        self.assertNotIn("promote", flags)
        self.assertNotIn("force", flags)
