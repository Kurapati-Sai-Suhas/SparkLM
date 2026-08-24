"""
The shared execution adapter (M2 P2.7 follow-up).

Three layers, deliberately:

1. the adapter's own semantics, as pure functions;
2. the REAL consumers — `GradingService.grade` and `OracleService.run` — driven
   through a capturing runner, because a test that reimplements the parser
   proves only that the test agrees with itself;
3. end-to-end EXECUTION of the source those consumers actually produced, run
   locally through subprocess, which is the only way to know the envelope
   really binds the arguments the signature declares.

Layer 3 runs local CPython on synthetic code. No Judge0, no production data,
no reference rows, no database — `SimpleTestCase` fails on any query.
"""

import json
import subprocess
import sys

from django.test import SimpleTestCase

from groups import execution_adapter as adapter
from groups import execution_contract, oracle, services
from groups.models import Question, ReferenceSolution, compute_source_hash
from groups.services import ExecutionContractError, GradingService


# ── Fixtures ────────────────────────────────────────────────────────────────

def starter(parameters, body="        pass", name="solve"):
    return (f"class Solution:\n"
            f"    def {name}(self, {parameters}):\n{body}\n")


ONE_TEXT = starter("s: str")
ONE_INT = starter("n: int")
ONE_LIST_INT = starter("nums: list[int]")
ONE_LIST_STR = starter("words: list[str]")
ONE_DICT = starter("d: dict")
ONE_BOOL = starter("flag: bool")
ONE_FLOAT = starter("x: float")
TWO_SCALARS = starter("a: int, b: int")
TWO_TEXT = starter("a: str, b: str")
MIXED = starter("s: str, nums: list[int]")
UNANNOTATED = starter("s")
VARIADIC = starter("*args, **kwargs")
ZERO_ARGS = "class Solution:\n    def solve(self):\n        pass\n"
KEYWORD_ONLY = "class Solution:\n    def solve(self, *, n: int):\n        pass\n"


def build_question(**overrides):
    """UNSAVED. Never persisted, so no query is issued."""
    fields = {
        "pk": 900_001,
        "title": "adapter fixture",
        "hidden_test_cases": [{"stdin": "110", "expected_output": "true"}],
        "hidden_wrapper_code": {},
        "boilerplate_code": {"python": ONE_TEXT},
        "execution_contract_version": "v1",
    }
    fields.update(overrides)
    return Question(**fields)


def build_reference(question, source="class Solution:\n    def solve(self, s):\n        return s\n"):
    reference = ReferenceSolution(
        pk=900_002, language="python", source_code=source,
        review_state=ReferenceSolution.REVIEW_APPROVED, is_active=True,
        source_hash=compute_source_hash(source))
    reference.question = question
    return reference


class CapturingRunner:
    """Records (source, language, stdin). Executes nothing."""

    def __init__(self, stdout=""):
        self.calls = []
        self._stdout = stdout

    def __call__(self, source, language, stdin):
        self.calls.append((source, language, stdin))
        return {"status_id": 3, "status": "Accepted", "stdout": self._stdout}


def run_locally(source, stdin):
    """Execute generated wrapper source in a local CPython subprocess."""
    result = subprocess.run(
        [sys.executable, "-c", source], input=stdin,
        capture_output=True, text=True, timeout=30)
    return result.returncode, (result.stdout or "").strip(), result.stderr


# ── 1. Input adapter semantics ──────────────────────────────────────────────

class InputAdapterTests(SimpleTestCase):

    def arguments(self, stdin, source):
        invocation = adapter.build_invocation(stdin, source)
        self.assertTrue(invocation.ok,
                        f"refused: {invocation.outcome} {invocation.detail}")
        return invocation.arguments

    # class D — string parameters
    def test_numeric_looking_text_stays_text(self):
        for raw in ("110", "0", "000", "007", "1.0", "-7", "1e3",
                    "12345678901234567890"):
            self.assertEqual(self.arguments(raw, ONE_TEXT), [raw])

    def test_boolean_and_null_words_stay_text(self):
        for raw in ("true", "false", "null", "True", "None"):
            self.assertEqual(self.arguments(raw, ONE_TEXT), [raw])

    def test_quoted_text_keeps_its_quotes(self):
        self.assertEqual(self.arguments('"0"', ONE_TEXT), ['"0"'])

    def test_whitespace_inside_text_is_data(self):
        for raw in ("  lead", "trail  ", "a b c", "\ttab", " "):
            self.assertEqual(self.arguments(raw, ONE_TEXT), [raw])

    def test_multi_line_text_is_one_argument(self):
        """A single text parameter is never split on newlines."""
        self.assertEqual(self.arguments("line1\nline2", ONE_TEXT),
                         ["line1\nline2"])

    def test_empty_text_argument(self):
        self.assertEqual(self.arguments("", ONE_TEXT), [""])

    # class C — one scalar
    def test_integer_parameter(self):
        self.assertEqual(self.arguments("42", ONE_INT), [42])
        self.assertEqual(self.arguments("  42  ", ONE_INT), [42])

    def test_float_parameter(self):
        self.assertEqual(self.arguments("1.5", ONE_FLOAT), [1.5])

    def test_boolean_parameter_accepts_both_spellings(self):
        self.assertEqual(self.arguments("true", ONE_BOOL), [True])
        self.assertEqual(self.arguments("False", ONE_BOOL), [False])

    def test_a_non_numeric_value_for_an_int_parameter_is_refused(self):
        invocation = adapter.build_invocation("abc", ONE_INT)
        self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH)

    def test_a_float_for_an_int_parameter_is_refused_not_truncated(self):
        """
        `int(float("1.5"))` is 1. Truncating would answer a different question
        from the one the test case asks, and would do it silently.
        """
        for raw in ("1.5", "2.0", "1e3"):
            invocation = adapter.build_invocation(raw, ONE_INT)
            self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH, raw)

    def test_only_json_and_python_boolean_spellings_are_accepted(self):
        """
        `yes`, `1` and `TRUE` are not booleans. Widening this would let a
        question about the string "yes" be answered as `True`.
        """
        for raw in ("yes", "no", "1", "0", "TRUE", "y", ""):
            invocation = adapter.build_invocation(raw, ONE_BOOL)
            self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH, raw)

    # class A — one list parameter, the 835-question defect
    def test_a_json_array_is_ONE_list_argument(self):
        self.assertEqual(self.arguments("[1,8,6,2,5,4,8,3,7]", ONE_LIST_INT),
                         [[1, 8, 6, 2, 5, 4, 8, 3, 7]])

    def test_space_separated_tokens_become_one_list_argument(self):
        self.assertEqual(self.arguments("2 7 11 15", ONE_LIST_INT),
                         [[2, 7, 11, 15]])

    def test_a_list_of_strings_keeps_string_elements(self):
        self.assertEqual(self.arguments('["flower","flow"]', ONE_LIST_STR),
                         [["flower", "flow"]])

    def test_nested_lists_survive(self):
        self.assertEqual(self.arguments("[[1,2],[3,4]]", ONE_LIST_INT),
                         [[[1, 2], [3, 4]]])

    def test_a_lone_token_is_a_one_element_sequence(self):
        """
        `5` and `2 7 11 15` are both stored forms for the same one-list
        parameter. Treating two cases of one question under different rules
        would be worse than either rule alone.
        """
        self.assertEqual(self.arguments("5", ONE_LIST_INT), [[5]])

    def test_an_object_for_a_list_parameter_is_refused(self):
        invocation = adapter.build_invocation('{"a":1}', ONE_LIST_INT)
        self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH)

    def test_a_non_integer_element_is_refused(self):
        invocation = adapter.build_invocation("1 two 3", ONE_LIST_INT)
        self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH)

    # class B — multiple scalars
    def test_two_scalars_from_two_lines(self):
        self.assertEqual(self.arguments("6\n7", TWO_SCALARS), [6, 7])

    def test_two_scalars_from_a_json_array(self):
        self.assertEqual(self.arguments("[6, 7]", TWO_SCALARS), [6, 7])

    def test_two_scalars_from_two_tokens(self):
        self.assertEqual(self.arguments("6 7", TWO_SCALARS), [6, 7])

    def test_two_bare_strings_reach_two_parameters(self):
        """Impossible under raw v1: `abc` is not JSON, so both lines collapsed
        into one argument."""
        self.assertEqual(self.arguments("abc\ndef", TWO_TEXT), ["abc", "def"])

    def test_wrong_arity_is_refused_not_guessed(self):
        invocation = adapter.build_invocation("1\n2\n3", TWO_SCALARS)
        self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH)
        self.assertIn("2 declared parameter", invocation.detail)

    # class F — mixed
    def test_mixed_scalar_and_list(self):
        self.assertEqual(self.arguments("catsanddog\n1 2 3", MIXED),
                         ["catsanddog", [1, 2, 3]])

    def test_a_list_of_strings_parameter_takes_word_tokens(self):
        self.assertEqual(
            self.arguments("catsanddog\ncat cats and", starter(
                "s: str, words: list[str]")),
            ["catsanddog", ["cat", "cats", "and"]])

    def test_word_tokens_for_a_list_of_ints_are_refused(self):
        invocation = adapter.build_invocation("x\ncat cats", MIXED)
        self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH)
        self.assertIn("not an integer", invocation.detail)

    def test_mixed_from_a_json_array(self):
        self.assertEqual(self.arguments('["abc", [1,2]]', MIXED),
                         ["abc", [1, 2]])

    def test_a_decoded_number_for_a_declared_text_parameter_is_refused(self):
        """The stored data makes a type claim the signature rules out."""
        invocation = adapter.build_invocation("[110, 3]", MIXED)
        self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH)
        self.assertIn("declared text", invocation.detail)

    # class H — zero arguments
    def test_zero_parameters_with_blank_stdin(self):
        self.assertEqual(self.arguments("", ZERO_ARGS), [])
        self.assertEqual(self.arguments("   \n  ", ZERO_ARGS), [])

    def test_zero_parameters_with_non_blank_stdin_is_refused(self):
        invocation = adapter.build_invocation("1", ZERO_ARGS)
        self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH)

    def test_blank_stdin_for_a_one_parameter_function_is_the_empty_string(self):
        """
        The §6 empty-input rule. With a DECLARED text parameter this is not
        ambiguous — it is "". The ambiguity only exists when nothing is
        declared, and that case is reported, not guessed.
        """
        self.assertEqual(self.arguments("", ONE_TEXT), [""])

    def test_blank_stdin_for_a_declared_int_is_refused(self):
        invocation = adapter.build_invocation("", ONE_INT)
        self.assertEqual(invocation.outcome, adapter.CONTRACT_MISMATCH)

    # class E / G — refusals
    def test_variadic_starters_are_refused(self):
        invocation = adapter.build_invocation("[1,2]", VARIADIC)
        self.assertEqual(invocation.outcome, adapter.NEEDS_MANUAL_REVIEW)

    def test_keyword_only_parameters_are_refused(self):
        invocation = adapter.build_invocation("1", KEYWORD_ONLY)
        self.assertEqual(invocation.outcome, adapter.NEEDS_MANUAL_REVIEW)

    def test_a_starter_with_no_callable_is_refused(self):
        invocation = adapter.build_invocation("x = 1", "x = 1")
        self.assertEqual(invocation.outcome, adapter.NEEDS_MANUAL_REVIEW)

    # unannotated
    def test_unannotated_parameters_execute_but_are_flagged(self):
        invocation = adapter.build_invocation("110", UNANNOTATED)
        self.assertTrue(invocation.ok)
        self.assertEqual(invocation.arguments, [110])
        self.assertIn("undeclared_parameter_type", invocation.warnings)

    def test_legacy_guessing_matches_json_spellings_exactly(self):
        """
        The undeclared path reproduces v1, and v1 recognises the JSON words
        only. Widening it to `None`/`TRUE` would change how existing questions
        behave the moment they adopt v3 — a migration must not smuggle in a
        second semantic change.
        """
        for raw in ("None", "TRUE", "True", "Null", "nil"):
            self.assertEqual(
                adapter.build_invocation(raw, UNANNOTATED).arguments, [raw])
        self.assertEqual(
            adapter.build_invocation("null", UNANNOTATED).arguments, [None])

    def test_list_of_str_elements_are_not_retyped(self):
        """Numeric-looking words in a declared `list[str]` stay words."""
        self.assertEqual(
            self.arguments("110 007 true", ONE_LIST_STR),
            [["110", "007", "true"]])

    # structural
    def test_non_text_stdin_is_invalid_input_not_a_mismatch(self):
        invocation = adapter.build_invocation([1, 2], ONE_LIST_INT)
        self.assertEqual(invocation.outcome, adapter.INVALID_INPUT)

    def test_literal_backslash_n_is_expanded(self):
        self.assertEqual(self.arguments("abc\\ndef", TWO_TEXT), ["abc", "def"])

    def test_a_refusal_has_no_envelope(self):
        invocation = adapter.build_invocation("1", ZERO_ARGS)
        with self.assertRaises(ValueError):
            invocation.envelope()


class AnnotationClassificationTests(SimpleTestCase):

    def test_containers_win_over_their_elements(self):
        self.assertEqual(adapter.classify_annotation("list[str]"), adapter.SEQUENCE)
        self.assertEqual(adapter.classify_annotation("dict[str,int]"), adapter.MAPPING)

    def test_bool_is_not_read_as_int(self):
        """Python's bool IS an int; the looser test would swallow it."""
        self.assertEqual(adapter.classify_annotation("bool"), adapter.BOOLEAN)

    def test_scalars(self):
        self.assertEqual(adapter.classify_annotation("str"), adapter.TEXT)
        self.assertEqual(adapter.classify_annotation("int"), adapter.INTEGER)
        self.assertEqual(adapter.classify_annotation("float"), adapter.FLOAT)
        self.assertEqual(adapter.classify_annotation("optional[str]"), adapter.TEXT)
        self.assertEqual(adapter.classify_annotation(""), adapter.UNDECLARED)

    def test_element_kinds(self):
        self.assertEqual(adapter.element_kind("list[int]"), adapter.INTEGER)
        self.assertEqual(adapter.element_kind("list[str]"), adapter.TEXT)
        self.assertEqual(adapter.element_kind("list"), adapter.UNDECLARED)


# ── 2. Output adapter ───────────────────────────────────────────────────────

class OutputAdapterTests(SimpleTestCase):

    def test_booleans_are_lowercase(self):
        self.assertEqual(adapter.canonical_output(True), "true")
        self.assertEqual(adapter.canonical_output(False), "false")

    def test_one_is_not_true(self):
        self.assertEqual(adapter.canonical_output(1), "1")
        self.assertNotEqual(adapter.canonical_output(1),
                            adapter.canonical_output(True))

    def test_none_renders_as_null(self):
        self.assertEqual(adapter.canonical_output(None), "null")

    def test_numbers(self):
        self.assertEqual(adapter.canonical_output(42), "42")
        self.assertEqual(adapter.canonical_output(1.5), "1.5")
        self.assertEqual(adapter.canonical_output(3.0), "3.0")

    def test_containers_are_compact_json(self):
        self.assertEqual(adapter.canonical_output([1, 2, 3]), "[1,2,3]")
        self.assertEqual(adapter.canonical_output({"a": 1}), '{"a":1}')
        self.assertEqual(adapter.canonical_output([True, False]), "[true,false]")

    def test_spaces_inside_strings_are_not_stripped(self):
        """
        The shipped wrapper prints `json.dumps(res).replace(" ", "")`, which
        turns `["a b"]` into `["ab"]` — it corrupts the content while trying to
        remove padding. `separators` does only the latter.
        """
        self.assertEqual(adapter.canonical_output(["a b"]), '["a b"]')

    def test_comparison_normalises_whitespace_only(self):
        self.assertTrue(adapter.outputs_match("42\r\n", "42"))
        self.assertTrue(adapter.outputs_match("a \nb", "a\nb"))
        self.assertTrue(adapter.outputs_match("x\n\n", "x"))

    def test_comparison_does_not_make_wrong_answers_equal(self):
        """The failure mode a permissive comparator creates."""
        self.assertFalse(adapter.outputs_match("True", "true"))
        self.assertFalse(adapter.outputs_match("1 2", "12"))
        self.assertFalse(adapter.outputs_match("1", "1.0"))
        self.assertFalse(adapter.outputs_match("[1,2]", "[2,1]"))
        self.assertFalse(adapter.outputs_match("abc", "ABC"))
        self.assertFalse(adapter.outputs_match("", "0"))

    def test_python_cased_booleans_are_not_canonical(self):
        self.assertFalse(adapter.is_canonical_output("True"))
        self.assertFalse(adapter.is_canonical_output("False"))
        self.assertTrue(adapter.is_canonical_output("true"))
        self.assertTrue(adapter.is_canonical_output("[1,2]"))


# ── 3. Safe test-case reading (the 48 crashes) ──────────────────────────────

class TestCaseReadingTests(SimpleTestCase):

    def test_a_well_formed_case_reads_cleanly(self):
        stdin, expected, problem = adapter.read_test_case(
            {"stdin": "1", "expected_output": "2"})
        self.assertEqual((stdin, expected, problem), ("1", "2", None))

    def test_missing_fields_default_to_empty_text(self):
        stdin, expected, problem = adapter.read_test_case({})
        self.assertEqual((stdin, expected, problem), ("", "", None))

    def test_none_fields_do_not_crash(self):
        stdin, expected, problem = adapter.read_test_case(
            {"stdin": None, "expected_output": None})
        self.assertIsNone(problem)
        self.assertEqual((stdin, expected), ("", ""))

    def test_a_list_valued_expected_output_is_reported(self):
        _stdin, _expected, problem = adapter.read_test_case(
            {"stdin": "x", "expected_output": ["a"]})
        self.assertIn("expected_output is a list", problem)

    def test_a_list_valued_stdin_is_reported(self):
        _stdin, _expected, problem = adapter.read_test_case(
            {"stdin": [1], "expected_output": "x"})
        self.assertIn("stdin is a list", problem)

    def test_a_non_dict_case_is_reported(self):
        _stdin, _expected, problem = adapter.read_test_case("junk")
        self.assertIn("not an object", problem)


# ── 4. The REAL consumers ───────────────────────────────────────────────────

class GradingServiceTests(SimpleTestCase):
    """`GradingService.grade` itself — not a reimplementation of it."""

    def test_v1_stdin_is_byte_for_byte_unchanged(self):
        """
        The whole safety claim. Every production question is v1, so if this
        drifts, ~2,900 questions are silently re-graded.
        """
        question = build_question(
            hidden_test_cases=[{"stdin": "2 7 11 15\\n9",
                                "expected_output": "0 1"}])
        runner = CapturingRunner(stdout="0 1")
        GradingService(runner).grade(question, "python", "code")
        self.assertEqual(runner.calls[0][2], "2 7 11 15\n9")

    def test_v2_stdin_is_byte_for_byte_unchanged(self):
        question = build_question(execution_contract_version="v2",
                                  hidden_test_cases=[{"stdin": "a\\nb",
                                                      "expected_output": "x"}])
        runner = CapturingRunner(stdout="x")
        GradingService(runner).grade(question, "python", "code")
        self.assertEqual(runner.calls[0][2], "a\nb")

    def test_v3_sends_the_canonical_envelope(self):
        question = build_question(
            execution_contract_version="v3",
            boilerplate_code={"python": ONE_TEXT},
            hidden_test_cases=[{"stdin": "110", "expected_output": "3"}])
        runner = CapturingRunner(stdout="3")
        GradingService(runner).grade(question, "python", "code")
        self.assertEqual(runner.calls[0][2], '["110"]')

    def test_v3_wraps_a_list_as_one_argument(self):
        question = build_question(
            execution_contract_version="v3",
            boilerplate_code={"python": ONE_LIST_INT},
            hidden_test_cases=[{"stdin": "[1,2,3]", "expected_output": "6"}])
        runner = CapturingRunner(stdout="6")
        GradingService(runner).grade(question, "python", "code")
        self.assertEqual(json.loads(runner.calls[0][2]), [[1, 2, 3]])

    def test_v3_uses_v1s_template_unchanged(self):
        question = build_question(execution_contract_version="v3")
        v1_source, _ = GradingService._build_executable(
            build_question(), "python", "code")
        v3_source, _ = GradingService._build_executable(question, "python", "code")
        self.assertEqual(v1_source, v3_source)

    def test_a_list_valued_expected_output_refuses_instead_of_crashing(self):
        """
        Was `AttributeError: 'list' object has no attribute 'strip'` — an
        unhandled 500 with no verdict. Now a named refusal.
        """
        question = build_question(
            hidden_test_cases=[{"stdin": "x", "expected_output": ["a", "b"]}])
        with self.assertRaises(ExecutionContractError) as caught:
            GradingService(CapturingRunner()).grade(question, "python", "code")
        self.assertIn("expected_output", str(caught.exception))

    def test_a_list_valued_stdin_refuses_instead_of_crashing(self):
        question = build_question(
            hidden_test_cases=[{"stdin": [1, 2], "expected_output": "x"}])
        with self.assertRaises(ExecutionContractError):
            GradingService(CapturingRunner()).grade(question, "python", "code")

    def test_a_broken_case_is_never_scored_as_a_verdict(self):
        """
        Refusing is the point. `wrong_answer` would tell a learner their
        correct solution failed; `accepted` would mint grading truth from a
        test nobody can run.
        """
        question = build_question(
            hidden_test_cases=[{"stdin": "x", "expected_output": ["a"]}])
        runner = CapturingRunner()
        with self.assertRaises(ExecutionContractError):
            GradingService(runner).grade(question, "python", "code")
        self.assertEqual(runner.calls, [], "the runner must not have been called")

    def test_a_v3_contract_mismatch_refuses(self):
        question = build_question(
            execution_contract_version="v3",
            boilerplate_code={"python": TWO_SCALARS},
            hidden_test_cases=[{"stdin": "1\\n2\\n3", "expected_output": "x"}])
        with self.assertRaises(ExecutionContractError) as caught:
            GradingService(CapturingRunner()).grade(question, "python", "code")
        self.assertIn("CONTRACT_MISMATCH", str(caught.exception))

    def test_v3_refuses_a_non_python_language(self):
        question = build_question(execution_contract_version="v3")
        with self.assertRaises(ExecutionContractError):
            GradingService.prepare_stdin(question, "java", "1")


class SharedSeamTests(SimpleTestCase):
    """
    Grader and oracle must traverse the SAME seam, not two agreeing copies.
    """

    def both_calls(self, question, stdin):
        reference = build_reference(question)
        grading_runner = CapturingRunner(stdout="ok")
        services.GradingService(grading_runner).grade(
            question, "python", reference.source_code)
        oracle_runner = CapturingRunner(stdout="ok")
        oracle.OracleService(oracle_runner).run(
            question, reference, stdin, verify_determinism=False)
        return grading_runner.calls[0], oracle_runner.calls[0]

    def test_identical_source_and_stdin_under_v1(self):
        question = build_question(
            hidden_test_cases=[{"stdin": "2 7\\n9", "expected_output": "ok"}])
        graded, oracled = self.both_calls(question, "2 7\\n9")
        self.assertEqual(graded, oracled)

    def test_identical_source_and_stdin_under_v3(self):
        question = build_question(
            execution_contract_version="v3",
            boilerplate_code={"python": ONE_TEXT},
            hidden_test_cases=[{"stdin": "007", "expected_output": "ok"}])
        graded, oracled = self.both_calls(question, "007")
        self.assertEqual(graded, oracled)
        self.assertEqual(graded[2], '["007"]')

    def test_the_oracle_calls_the_graders_own_preparer(self):
        """
        AST, real Call nodes only — a docstring naming `prepare_stdin` must not
        satisfy this, and a private reimplementation must not slip past it.
        """
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(oracle))
        called = {f"{node.func.value.id}.{node.func.attr}"
                  for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)}
        self.assertIn("GradingService.prepare_stdin", called)
        self.assertIn("GradingService._build_executable", called)

    def test_the_oracle_no_longer_transforms_stdin_itself(self):
        """
        The duplicated rule is gone, not merely shadowed. By AST — the comment
        explaining the removal quotes the old code, so a text search finds it
        and passes for the wrong reason.
        """
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(oracle.OracleService._execute)))
        methods = {node.func.attr for node in ast.walk(tree)
                   if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute)}
        self.assertNotIn("replace", methods)
        self.assertIn("prepare_stdin", methods)


# ── 5. End-to-end: execute what the consumers produced ──────────────────────

class EndToEndExecutionTests(SimpleTestCase):
    """
    Local CPython, synthetic code. Proves the envelope really binds the
    arguments the signature declares — the capturing tests above only prove
    what was SENT.
    """

    def grade_and_execute(self, question, learner_source, expected_stdout):
        runner = CapturingRunner()
        try:
            GradingService(runner).grade(question, "python", learner_source)
        except ExecutionContractError:
            raise
        source, _language, stdin = runner.calls[0]
        code, stdout, stderr = run_locally(source, stdin)
        self.assertEqual(code, 0, f"non-zero exit: {stderr[:300]}")
        self.assertEqual(stdout, expected_stdout)

    def v3(self, starter_source, stdin):
        return build_question(
            execution_contract_version="v3",
            boilerplate_code={"python": starter_source},
            hidden_test_cases=[{"stdin": stdin, "expected_output": "x"}])

    def test_a_declared_string_arrives_as_a_string(self):
        self.grade_and_execute(
            self.v3(ONE_TEXT, "110"),
            "class Solution:\n    def solve(self, s: str):\n        return len(s)",
            "3")

    def test_leading_zeros_survive_execution(self):
        self.grade_and_execute(
            self.v3(ONE_TEXT, "007"),
            "class Solution:\n    def solve(self, s: str):\n        return s",
            "007")

    def test_a_list_arrives_as_ONE_argument(self):
        """The 835-question defect, executed."""
        self.grade_and_execute(
            self.v3(ONE_LIST_INT, "[1,8,6,2,5,4,8,3,7]"),
            "class Solution:\n    def solve(self, nums: list):\n        return len(nums)",
            "9")

    def test_two_bare_strings_arrive_as_two_arguments(self):
        self.grade_and_execute(
            self.v3(TWO_TEXT, "abc\\ndef"),
            "class Solution:\n    def solve(self, a: str, b: str):\n        return a + b",
            "abcdef")

    def test_space_separated_tokens_become_one_list(self):
        self.grade_and_execute(
            self.v3(ONE_LIST_INT, "2 7 11 15"),
            "class Solution:\n    def solve(self, nums: list):\n        return sum(nums)",
            "35")

    def test_zero_argument_invocation(self):
        self.grade_and_execute(
            self.v3(ZERO_ARGS, ""),
            "class Solution:\n    def solve(self):\n        return 7",
            "7")

    def test_mixed_scalar_and_list(self):
        self.grade_and_execute(
            self.v3(MIXED, "cat\\n1 2 3"),
            "class Solution:\n    def solve(self, s: str, nums: list):\n"
            "        return s + str(sum(nums))",
            "cat6")

    def test_the_same_question_under_v1_gets_the_WRONG_type(self):
        """
        The control. Identical question, identical learner code, v1 instead of
        v3: the string parameter arrives as an int and `len()` raises. This is
        what production does today, and it is why the envelope matters.
        """
        question = build_question(
            execution_contract_version="v1",
            boilerplate_code={"python": ONE_TEXT},
            hidden_test_cases=[{"stdin": "110", "expected_output": "3"}])
        runner = CapturingRunner()
        GradingService(runner).grade(question, "python", "code")
        source, _language, stdin = runner.calls[0]
        source = source.replace(
            "code",
            "class Solution:\n    def solve(self, s: str):\n        return len(s)")
        code, _stdout, stderr = run_locally(source, stdin)
        self.assertNotEqual(code, 0)
        self.assertIn("Runtime Error", stderr)


class ScopeTests(SimpleTestCase):
    """This phase must not have changed how any existing question grades."""

    #: sha256[:16] of every wrapper template, taken from git HEAD before this
    #: phase touched anything. A digest, not a substring check: a diff review
    #: can miss one character inside a 2KB string literal, and v1 is the
    #: harness ~2,900 production questions were graded under.
    TEMPLATE_DIGESTS = {
        "GENERIC_PYTHON_WRAPPER": "85766c03fbbd4d00",
        "GENERIC_JAVA_WRAPPER": "ede876af69ecbc58",
        "GENERIC_JS_WRAPPER": "18f29dad1e7afef8",
        "V2_PYTHON_WRAPPER": "efc0cac8e35e23ff",
        "V2_JAVA_WRAPPER": "9b0f0e0c50fc577d",
        "V2_JS_WRAPPER": "7d4a1acaf126ca42",
    }

    def test_every_wrapper_template_is_byte_for_byte_unchanged(self):
        import hashlib
        for name, expected in self.TEMPLATE_DIGESTS.items():
            template = getattr(services, name, None) or getattr(
                execution_contract, name)
            actual = hashlib.sha256(
                template.encode("utf-8")).hexdigest()[:16]
            self.assertEqual(actual, expected, f"{name} changed")

    def test_the_v1_template_still_contains_the_branch_v3_relies_on(self):
        """The splat is the mechanism. If it goes, v3 silently stops working."""
        self.assertIn("target_method(*parsed_input)",
                      services.GENERIC_PYTHON_WRAPPER)

    def test_no_template_consumes_the_adapter(self):
        """The adapter runs server-side; nothing was injected into the sandbox."""
        for template in (services.GENERIC_PYTHON_WRAPPER,
                         services.GENERIC_JAVA_WRAPPER,
                         services.GENERIC_JS_WRAPPER,
                         execution_contract.V2_PYTHON_WRAPPER):
            self.assertNotIn("execution_adapter", template)

    def test_the_adapter_is_pure(self):
        import ast
        import inspect
        import pathlib
        tree = ast.parse(
            pathlib.Path(inspect.getfile(adapter)).read_text("utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported, {"ast", "json"})

    def test_the_superseded_input_contract_model_is_not_wired_anywhere(self):
        """
        `groups/input_contract.py` was the previous phase's unwired semantic
        model. `execution_adapter` supersedes it and is the one that actually
        runs. Two coercion models in one codebase is the drift hazard this
        phase exists to remove, so the old one must stay unreferenced by
        production code until it is deleted.
        """
        import ast
        import pathlib
        production = [
            p for p in pathlib.Path(services.__file__).parent.glob("*.py")
            if not p.name.startswith("test_")]
        importers = []
        for path in production:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                if any("input_contract" in (n or "") for n in names):
                    importers.append(path.name)
        self.assertEqual(importers, [])

    def test_v1_is_still_the_default_for_an_undeclared_question(self):
        question = build_question()
        del question.execution_contract_version
        question.execution_contract_version = ""
        self.assertEqual(execution_contract.contract_version(question), "v1")
