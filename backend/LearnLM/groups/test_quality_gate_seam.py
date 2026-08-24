"""
The quality gate executes through the canonical seam (M2 P2.7 follow-up).

`hidden_test_quality._run_case` used to call

    runner(mutant.source, mutant.language, case.get("stdin", ""))

which bypassed BOTH halves of the shared execution seam: the mutant was never
wrapped by `GradingService._build_executable`, and the stored stdin never
passed through `prepare_stdin`. Tier-1 and Tier-2 kill rates were therefore
measured against semantics no learner ever experiences.

These tests drive the REAL `GradingService.quality_execution_plan` and execute
the source it produces in a local CPython subprocess, so a mutant here receives
exactly what a submission would. The decisive case is
`test_raw_and_canonical_execution_disagree`: it pins an input where the two
paths give different answers, so the bypass cannot come back unnoticed.

No Judge0, no production, no database — `SimpleTestCase` fails on any query.
"""

import subprocess
import sys

from django.test import SimpleTestCase

from groups import hidden_test_quality as q
from groups.models import Question
from groups.services import GradingService

TEXT_STARTER = "class Solution:\n    def solve(self, s: str) -> int:\n        pass\n"
LIST_STARTER = "class Solution:\n    def solve(self, nums: list[int]) -> int:\n        pass\n"
TWO_TEXT_STARTER = ("class Solution:\n"
                    "    def solve(self, a: str, b: str) -> str:\n        pass\n")
ZERO_ARG_STARTER = "class Solution:\n    def solve(self) -> int:\n        pass\n"


def build_question(starter=TEXT_STARTER, contract="v3", **overrides):
    """UNSAVED — never persisted, so no query is issued."""
    fields = {
        "pk": 940_001,
        "title": "seam fixture",
        "hidden_test_cases": [],
        "hidden_wrapper_code": {},
        "boilerplate_code": {"python": starter},
        "execution_contract_version": contract,
    }
    fields.update(overrides)
    return Question(**fields)


def local_runner(source, language, stdin):
    """Executes the source it is given, locally. Judge0's contract, no Judge0."""
    result = subprocess.run([sys.executable, "-c", source], input=stdin,
                            capture_output=True, text=True, timeout=30)
    return {"status_id": 3 if result.returncode == 0 else 11,
            "status": "Accepted" if result.returncode == 0 else "Runtime Error",
            "stdout": result.stdout, "stderr": result.stderr,
            "compile_output": ""}


def capturing_runner(sink):
    def _runner(source, language, stdin):
        sink.append((source, language, stdin))
        return {"status_id": 3, "status": "Accepted", "stdout": "", "stderr": ""}
    return _runner


def mutant(source, identifier="t1-x", tier=1):
    return q.Mutant(identifier=identifier, tier=tier,
                    description="seam probe", source=source, language="python")


CONTRACT = q.InputContract(numeric=True, is_sequence=False)


def run_one(question, source, stdin, expected, runner=local_runner):
    """One mutant, one case, through the real plan. Returns the MutantResult."""
    plan = GradingService.quality_execution_plan(question)
    return q.evaluate_mutant(
        runner, plan, mutant(source),
        [{"stdin": stdin, "expected_output": expected}])


# ── The decisive test ───────────────────────────────────────────────────────

class RawVersusCanonicalTests(SimpleTestCase):

    def test_raw_and_canonical_execution_disagree(self):
        """
        The bypass and the fix must be distinguishable, or nothing here proves
        anything. A question declaring `s: str` with stdin `110`:

          raw       -> the wrapper json-parses it and calls solve(110)   -> int
          canonical -> the adapter sends ["110"] and calls solve("110")  -> str

        `len(s)` is 3 under canonical execution and raises under raw. If the
        gate ever reverts, this test fails.
        """
        question = build_question()
        solution = ("class Solution:\n"
                    "    def solve(self, s: str) -> int:\n"
                    "        return len(s)\n")

        # Canonical: the plan wraps the solution and sends ["110"].
        plan = GradingService.quality_execution_plan(question)
        canonical = local_runner(plan.build_executable(solution, "python"),
                                 "python",
                                 plan.prepare_stdin("110", "python"))
        self.assertEqual(canonical["stdout"].strip(), "3",
                         "canonical execution should reach len('110') == 3")

        # Raw: exactly what the gate used to do — unwrapped source, raw stdin.
        raw = local_runner(solution, "python", "110")
        self.assertNotEqual(raw["stdout"].strip(), "3")

    def test_the_gate_itself_follows_the_canonical_result(self):
        """
        The same input, through the gate. A CORRECT solution agrees with the
        correct key, so the mutant SURVIVES — which is the gate reporting
        "this suite does not distinguish this behaviour", not a pass/fail of
        the solution. What matters here is that it ran at all and agreed.
        """
        result = run_one(
            build_question(),
            "class Solution:\n    def solve(self, s: str) -> int:\n        return len(s)",
            "110", "3")
        self.assertEqual(result.outcome, q.SURVIVED, result.detail)

    def test_a_wrong_solution_is_killed_under_canonical_execution(self):
        """The other half: the gate must still detect a genuine difference."""
        result = run_one(
            build_question(),
            "class Solution:\n    def solve(self, s: str) -> int:\n        return len(s) + 1",
            "110", "3")
        self.assertEqual(result.outcome, q.KILLED, result.detail)

    def test_the_plan_wraps_the_mutant_and_prepares_the_stdin(self):
        """Both halves of the seam, observed at the runner boundary."""
        question = build_question()
        sink = []
        run_one(question, "class Solution:\n    def solve(self, s): return s",
                "110", "110", runner=capturing_runner(sink))

        source, language, stdin = sink[0]
        self.assertIn("__main__", source, "mutant was not wrapped")
        self.assertEqual(language, "python")
        self.assertEqual(stdin, '["110"]', "stdin was not canonically prepared")


# ── Phase 3 input matrix, executed end to end ───────────────────────────────

class CanonicalInputMatrixTests(SimpleTestCase):
    """Each row runs a CORRECT solution and asserts the mutant is KILLED only
    when the stored key genuinely differs — i.e. the input arrived typed."""

    def assert_correct_solution_agrees(self, question, solution, stdin, expected):
        result = run_one(question, solution, stdin, expected)
        self.assertEqual(
            result.outcome, q.SURVIVED,
            f"a correct solution disagreed with the key: {result.detail}")

    def test_string_numeric_input(self):
        self.assert_correct_solution_agrees(
            build_question(),
            "class Solution:\n    def solve(self, s: str): return len(s)",
            "110", "3")

    def test_leading_zero_string(self):
        self.assert_correct_solution_agrees(
            build_question(),
            "class Solution:\n    def solve(self, s: str): return s",
            "007", "007")

    def test_quoted_string_is_characters(self):
        self.assert_correct_solution_agrees(
            build_question(),
            "class Solution:\n    def solve(self, s: str): return len(s)",
            '"0"', "3")

    def test_integer_input(self):
        self.assert_correct_solution_agrees(
            build_question(starter="class Solution:\n    def solve(self, n: int): pass"),
            "class Solution:\n    def solve(self, n: int): return n * 2",
            "21", "42")

    def test_float_input(self):
        self.assert_correct_solution_agrees(
            build_question(starter="class Solution:\n    def solve(self, x: float): pass"),
            "class Solution:\n    def solve(self, x: float): return x * 2",
            "1.5", "3.0")

    def test_bool_input(self):
        self.assert_correct_solution_agrees(
            build_question(starter="class Solution:\n    def solve(self, flag: bool): pass"),
            "class Solution:\n    def solve(self, flag: bool): return not flag",
            "true", "false")

    def test_null_input(self):
        self.assert_correct_solution_agrees(
            build_question(starter="class Solution:\n    def solve(self, v): pass"),
            "class Solution:\n    def solve(self, v): return v is None",
            "null", "true")

    def test_list_input_is_one_argument(self):
        self.assert_correct_solution_agrees(
            build_question(starter=LIST_STARTER),
            "class Solution:\n    def solve(self, nums: list): return sum(nums)",
            "[1,8,6]", "15")

    def test_object_input_is_one_argument(self):
        self.assert_correct_solution_agrees(
            build_question(starter="class Solution:\n    def solve(self, d: dict): pass"),
            "class Solution:\n    def solve(self, d: dict): return d['a']",
            '{"a": 5}', "5")

    def test_multiple_arguments(self):
        self.assert_correct_solution_agrees(
            build_question(starter=TWO_TEXT_STARTER),
            "class Solution:\n    def solve(self, a: str, b: str): return a + b",
            "abc\\ndef", "abcdef")

    def test_zero_arguments(self):
        self.assert_correct_solution_agrees(
            build_question(starter=ZERO_ARG_STARTER),
            "class Solution:\n    def solve(self): return 7",
            "", "7")

    def test_multiline_string_stays_one_argument(self):
        self.assert_correct_solution_agrees(
            build_question(),
            "class Solution:\n    def solve(self, s: str): return len(s)",
            "ab\\ncd", "5")

    def test_space_separated_tokens_become_a_list(self):
        self.assert_correct_solution_agrees(
            build_question(starter=LIST_STARTER),
            "class Solution:\n    def solve(self, nums: list): return sum(nums)",
            "2 7 11 15", "35")


class ContractFailureTests(SimpleTestCase):
    """A case the contract cannot express is UNMEASURED, never a silent pass."""

    def test_a_contract_mismatch_is_an_execution_error(self):
        question = build_question(starter=TWO_TEXT_STARTER)
        result = run_one(question,
                         "class Solution:\n    def solve(self, a, b): return a",
                         "1\\n2\\n3", "x")
        self.assertEqual(result.outcome, q.EXECUTION_ERROR)
        self.assertIn("execution contract", result.detail)

    def test_an_execution_error_blocks_the_whole_gate(self):
        question = build_question(starter=TWO_TEXT_STARTER)
        cases = [{"stdin": "1\\n2\\n3", "expected_output": "x"}]
        report = q.evaluate_suite(
            cases, [mutant("class Solution:\n    def solve(self, a, b): return a")],
            local_runner, CONTRACT,
            plan=GradingService.quality_execution_plan(question), floor=1)
        self.assertEqual(report.verdict, q.FAIL)
        self.assertTrue(any("could not be executed" in b for b in report.blockers))


# ── Structural guards ───────────────────────────────────────────────────────

class NoSecondParserTests(SimpleTestCase):
    """
    By AST, not text search — the module's own docstring names the bypass it
    removed, so a substring check would pass for the wrong reason.
    """

    def module_tree(self):
        import ast
        import inspect
        return ast.parse(inspect.getsource(q))

    def test_the_gate_never_parses_stdin_itself(self):
        import ast
        called = {node.func.attr for node in ast.walk(self.module_tree())
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        for forbidden in ("loads", "load", "dumps"):
            self.assertNotIn(forbidden, called,
                             f"the gate calls json.{forbidden} — second parser")

    def test_the_gate_imports_no_json_and_no_services(self):
        import ast
        imported = set()
        for node in ast.walk(self.module_tree()):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("json", imported)
        # Purity: the gate must not reach the ORM or the service layer.
        self.assertEqual(imported & {"django", "groups.services"}, set())

    def test_the_gate_builds_no_wrapper_of_its_own(self):
        """No template string may be constructed here — that is the seam's job."""
        import inspect
        source = inspect.getsource(q)
        for marker in ("{user_code}", "import sys", "sys.stdin"):
            self.assertNotIn(marker, source,
                             f"the gate appears to build a wrapper ({marker!r})")

    def test_run_case_reaches_the_runner_only_through_the_plan(self):
        """
        The exact defect, pinned structurally: `_run_case` must not pass
        `mutant.source` or a raw `case[...]` straight to the runner.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(q._run_case)))
        runner_calls = [node for node in ast.walk(tree)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "runner"]
        self.assertEqual(len(runner_calls), 1, "expected exactly one runner call")

        source_arg, _language_arg, stdin_arg = runner_calls[0].args
        # The source and the stdin must be locals the plan produced. The
        # language may stay `mutant.language` — it is a label, not input data.
        for argument, name in ((source_arg, "source"), (stdin_arg, "stdin")):
            self.assertIsInstance(
                argument, ast.Name,
                f"the {name} argument is an expression, not a plan-produced "
                f"local — that is how the bypass looked")
            self.assertEqual(argument.id, name)

    def test_the_plan_is_required(self):
        """No default — a default is how the bypass would return."""
        import inspect
        signature = inspect.signature(q.evaluate_suite)
        plan = signature.parameters["plan"]
        self.assertIs(plan.default, inspect.Parameter.empty)
        self.assertEqual(plan.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_production_has_exactly_one_plan_factory(self):
        import ast
        import inspect
        import pathlib

        root = pathlib.Path(q.__file__).parent
        builders = []
        for path in root.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "ExecutionPlan":
                    builders.append(path.name)
        self.assertEqual(builders, ["services.py"], builders)
