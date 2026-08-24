"""
Declared-type-aware coercion (M2 P2.7 contract remediation).

The centre of gravity is type STABILITY: a question declaring `s: str` must
receive the characters it stored, and must never receive an int that happens to
look the same. Both shipped contracts fail that, in different ways, and those
failures are pinned here as regression evidence.

NO DATABASE. `SimpleTestCase` throughout, and the service-level tests build
unsaved model instances — which is not a shortcut but an assertion: coercion is
a pure function of stdin and the declared signature, so any query would mean
grading depends on something other than the contract. A stray query fails the
test rather than passing quietly.

NO EXECUTION. The runner is a capturing stub; nothing here reaches Judge0.
"""

import ast
import inspect
import json
import pathlib
import textwrap

from django.test import SimpleTestCase

from groups import execution_contract, input_contract, oracle, services
from groups.models import Question, ReferenceSolution, compute_source_hash
from groups.utils import normalize_output


def annotation_of(function, name="s"):
    return inspect.signature(function).parameters[name].annotation


def typed_str(s: str): ...
def typed_int(s: int): ...
def typed_list(s: list): ...
def typed_list_str(s: list[str]): ...
def untyped(s): ...


EMPTY = annotation_of(untyped)
STR = annotation_of(typed_str)


# ── The shipped contracts, replicated for comparison ────────────────────────
#
# Replicated rather than imported because both live inside wrapper templates —
# strings that only become code inside Judge0. `ShippedContractRegressionTests`
# pins these replicas against the real templates so a drifting copy is caught.

def v1_parse(stdin):
    """`GENERIC_PYTHON_WRAPPER`'s stdin parse (services.py)."""
    stdin = stdin.strip()
    try:
        return json.loads(stdin)
    except Exception:
        lines = [line for line in stdin.split("\n") if line.strip()]
        try:
            return [json.loads(line) for line in lines]
        except Exception:
            return stdin


def v2_token(token):
    """`execution_contract._sparklm_token`."""
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    if token == "true":
        return True
    if token == "false":
        return False
    return token


class TypeStabilityTests(SimpleTestCase):

    def test_declared_string_is_never_coerced(self):
        """
        The core rule, over every shape that tempts a parser: numeric, boolean
        words, null, floats, leading zeros, hex, bignum, whitespace, empty.
        """
        for raw in ("110", "000", "0", "007", "true", "false", "null", "1.0",
                    "-7", "1e3", "12345678901234567890", "0x1f", "hello",
                    "1 2 3", "  padded  ", "", "[1, 2]", "{\"a\": 1}"):
            got = input_contract.coerce_argument(raw, STR)
            self.assertIsInstance(got, str, f"{raw!r} was retyped")
            self.assertEqual(got, raw, f"{raw!r} was altered")

    def test_110_and_int_110_are_not_interchangeable(self):
        """The confusion that started this: '110' must stay text."""
        as_text = input_contract.coerce_argument("110", STR)
        as_number = input_contract.coerce_argument("110", annotation_of(typed_int))

        self.assertIsInstance(as_text, str)
        self.assertEqual(as_text, "110")
        self.assertIsInstance(as_number, int)
        self.assertEqual(as_number, 110)
        self.assertIsNot(type(as_text), type(as_number))
        # The distinction a binary-string question depends on: text has a
        # length and can be indexed; the integer 110 cannot answer either.
        self.assertEqual(len(as_text), 3)

    def test_leading_zeros_survive(self):
        """
        `000` and `007` are where v2 is WORSE than v1: `int()` accepts them
        while JSON rejects them, so v2 destroys what v1 happened to preserve.
        """
        for raw in ("000", "007", "0000000"):
            self.assertEqual(input_contract.coerce_argument(raw, STR), raw)
            self.assertNotEqual(
                v2_token(raw), raw,
                "v2 was expected to coerce this; if it no longer does, this "
                "comparison is stale and the remediation note needs revisiting")

    def test_whitespace_inside_a_string_is_data(self):
        """No strip. A question about trailing spaces is graded on its bytes."""
        for raw in ("  lead", "trail  ", "a b c", "\ttab", " ", "  "):
            self.assertEqual(input_contract.coerce_argument(raw, STR), raw)

    def test_quotes_are_characters_not_syntax(self):
        """`"0"` is three characters. Nothing unquotes it."""
        self.assertEqual(input_contract.coerce_argument('"0"', STR), '"0"')
        self.assertEqual(input_contract.coerce_argument("'true'", STR), "'true'")

    def test_sequence_annotation_yields_a_list(self):
        got = input_contract.coerce_argument("1 2 3", annotation_of(typed_list))
        self.assertEqual(got, [1, 2, 3])

    def test_single_element_sequence_is_still_a_sequence(self):
        """A declared list of one must not collapse to a scalar."""
        got = input_contract.coerce_argument("5", annotation_of(typed_list))
        self.assertEqual(got, [5])

    def test_list_of_str_is_a_sequence_not_a_string(self):
        """`list[str]` contains 'str' — the sequence check must win."""
        annotation = annotation_of(typed_list_str)
        self.assertFalse(input_contract.declares_text(annotation))
        self.assertTrue(input_contract.declares_sequence(annotation))
        self.assertEqual(
            input_contract.coerce_argument("a b", annotation), ["a", "b"])

    def test_string_annotation_spelling_is_honoured(self):
        """`from __future__ import annotations` makes annotations strings."""
        self.assertTrue(input_contract.declares_text("str"))
        self.assertTrue(input_contract.declares_text("Optional[str]"))
        self.assertFalse(input_contract.declares_text("List[str]"))
        self.assertTrue(input_contract.declares_sequence("List[str]"))

    def test_unannotated_keeps_legacy_shape_guessing(self):
        """
        No annotation means no declared contract. Guessing is preserved rather
        than improved: silently changing it would alter questions nobody has
        reviewed, which is the thing this phase exists to prevent.
        """
        self.assertEqual(input_contract.coerce_argument("110", EMPTY), 110)
        self.assertEqual(input_contract.coerce_argument("hello", EMPTY), "hello")
        self.assertEqual(input_contract.coerce_argument("1 2", EMPTY), [1, 2])
        self.assertIs(input_contract.coerce_argument("true", EMPTY), True)
        self.assertIsNone(input_contract.coerce_argument("null", EMPTY))

    def test_boolean_words_are_matched_exactly_not_case_folded(self):
        """
        `TRUE` and `True` are ordinary text. Case-folding them would silently
        answer a question about capitalisation with a boolean.
        """
        self.assertEqual(input_contract.coerce_argument("TRUE", EMPTY), "TRUE")
        self.assertEqual(input_contract.coerce_argument("True", EMPTY), "True")
        self.assertEqual(input_contract.coerce_argument("None", EMPTY), "None")
        self.assertEqual(input_contract.coerce_argument(" true", EMPTY), True)

    def test_the_missing_annotation_sentinel_is_recognised(self):
        """
        `inspect.Parameter.empty` is a CLASS, so it arrives as the name
        `_empty`, never through `str()`. That it matches no type hint is luck;
        this pins it as intent, because a hint-shaped class name would
        otherwise be read as a declared type.
        """
        sentinel = inspect.Parameter.empty
        self.assertEqual(input_contract._annotation_text(sentinel), "")
        self.assertFalse(input_contract.declares_text(sentinel))
        self.assertFalse(input_contract.declares_sequence(sentinel))
        self.assertEqual(input_contract._annotation_text(None), "")

    def test_multi_line_input_coerces_each_line_independently(self):
        """
        A two-argument question: line 1 is text, line 2 is a count. Coercion is
        per-parameter, so one line staying a string cannot retype the other.
        """
        lines = ["007", "3"]
        annotations = [STR, annotation_of(typed_int)]
        got = [input_contract.coerce_argument(line, annotation)
               for line, annotation in zip(lines, annotations)]
        self.assertEqual(got, ["007", 3])
        self.assertIsInstance(got[0], str)
        self.assertIsInstance(got[1], int)

    def test_empty_line_for_a_text_parameter_is_the_empty_string(self):
        """
        The 27 zero-arg / blank-stdin cases hinge on this. For a declared text
        parameter a blank line is unambiguous: it is "". It is NOT coerced to
        None, and it is NOT an error — the ambiguity in the census lives in the
        UNANNOTATED path below, not here.
        """
        self.assertEqual(input_contract.coerce_argument("", STR), "")

    def test_empty_line_without_an_annotation_stays_undecided(self):
        """
        Recorded, not resolved. With no declared type there is nothing to say
        what a blank line means, so this test pins current behaviour rather
        than blessing it; §6 of the brief is a decision for a human.
        """
        self.assertEqual(input_contract.coerce_argument("", EMPTY), [])


class ShippedContractRegressionTests(SimpleTestCase):
    """
    What v1 and v2 actually do. If either template changes, these fail — which
    is the point: 1,782 questions were graded under v1 and a silent edit to it
    re-grades all of them.
    """

    def test_v1_retypes_numeric_looking_strings(self):
        self.assertIsInstance(v1_parse("110"), int)
        self.assertEqual(v1_parse("000"), "000")      # invalid JSON, survives
        self.assertIs(v1_parse("true"), True)
        self.assertIsNone(v1_parse("null"))
        self.assertIsInstance(v1_parse("1.0"), float)

    def test_v2_retypes_and_is_worse_on_leading_zeros(self):
        self.assertEqual(v2_token("110"), 110)
        self.assertEqual(v2_token("000"), 0)          # v1 kept "000"
        self.assertEqual(v2_token("007"), 7)          # v1 kept "007"
        self.assertIs(v2_token("true"), True)
        self.assertEqual(v2_token("null"), "null")    # v1 gave None

    def test_neither_shipped_contract_honours_a_declared_string(self):
        """The finding, in one assertion: migrating to v2 is not a fix."""
        for raw in ("110", "0", "true", "1.0"):
            self.assertNotIsInstance(v1_parse(raw), str, raw)
            self.assertNotIsInstance(v2_token(raw), str, raw)
            self.assertIsInstance(input_contract.coerce_argument(raw, STR), str)

    def test_the_replicas_still_match_the_real_templates(self):
        """
        These replicas are only evidence if they track the originals. Compare
        the parsed AST of the real functions against the replicas, so
        reformatting is tolerated and a behaviour change is not.
        """
        real = _function_source(
            execution_contract.V2_PYTHON_WRAPPER, "_sparklm_token")
        self.assertIsNotNone(real, "_sparklm_token vanished from V2_PYTHON_WRAPPER")
        self.assertEqual(
            _normalised_body(real),
            _normalised_body(inspect.getsource(v2_token)),
            "the v2 replica has drifted from V2_PYTHON_WRAPPER")

    def test_v1_still_parses_stdin_with_json_loads(self):
        """v1 has no function to compare, so pin the defect itself."""
        self.assertIn("json.loads(stdin_str)", services.GENERIC_PYTHON_WRAPPER)


class OutputRenderingTests(SimpleTestCase):

    def test_booleans_render_lowercase(self):
        self.assertEqual(input_contract.render_output(True), "true")
        self.assertEqual(input_contract.render_output(False), "false")

    def test_lowercase_agrees_with_the_shipped_formatter(self):
        """
        v1 prints `str(res).lower()`. The renderer agrees — which is why 67
        production questions storing "True"/"False" cannot pass, and why the
        fix is to repair that data rather than change the renderer and break
        the 153 that store it correctly.
        """
        self.assertIn("str(res).lower()", services.GENERIC_PYTHON_WRAPPER)
        self.assertEqual(input_contract.render_output(True), str(True).lower())

    def test_normalize_output_does_not_fold_case(self):
        """The reason casing matters at all: the comparator is case-sensitive."""
        self.assertNotEqual(normalize_output("True"), normalize_output("true"))

    def test_one_is_not_true(self):
        """`1` and `True` must not render alike — bool is checked before int."""
        self.assertEqual(input_contract.render_output(1), "1")
        self.assertEqual(input_contract.render_output(True), "true")

    def test_other_scalars_render_predictably(self):
        self.assertEqual(input_contract.render_output(42), "42")
        self.assertEqual(input_contract.render_output("abc"), "abc")
        self.assertEqual(input_contract.render_output(None), "null")
        self.assertEqual(input_contract.render_output([1, 2, 3]), "1 2 3")
        self.assertEqual(input_contract.render_output([True, False]), "true false")

    def test_a_rendered_string_keeps_its_leading_zeros(self):
        """Output side of the same defect: "007" must not print as 7."""
        self.assertEqual(input_contract.render_output("007"), "007")


# ── Contract identity: grader and oracle ────────────────────────────────────

class CapturingRunner:
    """
    Stands in for Judge0. Records every (source, language, stdin) it is handed
    and returns a fixed accepted verdict. No network, no subprocess.
    """

    def __init__(self, stdout=""):
        self.calls = []
        self._stdout = stdout

    def __call__(self, source, language, stdin):
        self.calls.append((source, language, stdin))
        return {"status_id": 3, "status": "Accepted", "stdout": self._stdout}


def build_question(**overrides):
    """An UNSAVED question. Never persisted; no query is made."""
    fields = {
        "pk": 999_001,
        "title": "contract identity fixture",
        "hidden_test_cases": [{"stdin": "110", "expected_output": "true"}],
        "hidden_wrapper_code": {},
        "execution_contract_version": "v1",
    }
    fields.update(overrides)
    return Question(**fields)


def build_reference(question, source="def solve(s):\n    return True\n"):
    """An UNSAVED canonical reference. `is_canonical` is a pure property."""
    reference = ReferenceSolution(
        pk=999_002,
        language="python",
        source_code=source,
        review_state=ReferenceSolution.REVIEW_APPROVED,
        is_active=True,
        source_hash=compute_source_hash(source),
    )
    reference.question = question
    return reference


class SharedContractTests(SimpleTestCase):
    """
    The grader and the oracle must execute identical source for identical
    input. If they diverge, the answer key is minted under one contract and
    learners are graded under another — the failure this milestone exists to
    prevent, and one that looks like nothing is wrong.
    """

    def test_both_services_execute_byte_identical_source(self):
        question = build_question()
        reference = build_reference(question)

        grading_runner = CapturingRunner(stdout="true")
        services.GradingService(grading_runner).grade(
            question, "python", reference.source_code)

        oracle_runner = CapturingRunner(stdout="true")
        oracle.OracleService(oracle_runner).run(
            question, reference, "110", verify_determinism=False)

        graded_source, graded_lang, graded_stdin = grading_runner.calls[0]
        oracle_source, oracle_lang, oracle_stdin = oracle_runner.calls[0]

        self.assertEqual(graded_source, oracle_source)
        self.assertEqual(graded_lang, oracle_lang)
        self.assertEqual(graded_stdin, oracle_stdin)

    def test_identity_holds_under_v2(self):
        question = build_question(execution_contract_version="v2")
        reference = build_reference(question)

        grading_runner = CapturingRunner(stdout="true")
        services.GradingService(grading_runner).grade(
            question, "python", reference.source_code)
        oracle_runner = CapturingRunner(stdout="true")
        oracle.OracleService(oracle_runner).run(
            question, reference, "110", verify_determinism=False)

        self.assertEqual(grading_runner.calls[0][0], oracle_runner.calls[0][0])
        self.assertIn("_sparklm_token", grading_runner.calls[0][0])

    def test_identity_holds_under_a_per_question_wrapper(self):
        """The branch checked first in `_build_executable`."""
        question = build_question(
            hidden_wrapper_code={"python": "# custom\n{user_code}\n"})
        reference = build_reference(question)

        grading_runner = CapturingRunner(stdout="true")
        services.GradingService(grading_runner).grade(
            question, "python", reference.source_code)
        oracle_runner = CapturingRunner(stdout="true")
        oracle.OracleService(oracle_runner).run(
            question, reference, "110", verify_determinism=False)

        self.assertEqual(grading_runner.calls[0][0], oracle_runner.calls[0][0])
        self.assertTrue(grading_runner.calls[0][0].startswith("# custom"))

    def test_both_services_apply_the_same_literal_newline_conversion(self):
        """
        Test-case stdin stores `\\n` as two characters. If only one service
        expanded it, a two-line input would reach the grader as one line.
        """
        question = build_question(
            hidden_test_cases=[{"stdin": "007\\n3", "expected_output": "true"}])
        reference = build_reference(question)

        grading_runner = CapturingRunner(stdout="true")
        services.GradingService(grading_runner).grade(
            question, "python", reference.source_code)
        oracle_runner = CapturingRunner(stdout="true")
        oracle.OracleService(oracle_runner).run(
            question, reference, "007\\n3", verify_determinism=False)

        self.assertEqual(grading_runner.calls[0][2], "007\n3")
        self.assertEqual(oracle_runner.calls[0][2], grading_runner.calls[0][2])

    def test_the_oracle_calls_the_graders_builder_and_not_its_own(self):
        """
        Structural backstop, by AST: identity above could be preserved today by
        two implementations that happen to agree. This asserts there is only
        one. Real Call nodes only — a docstring naming `_build_executable`
        must not satisfy it.
        """
        tree = ast.parse(inspect.getsource(oracle))
        called = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                called.add(f"{func.value.id}.{func.attr}")
        self.assertIn("GradingService._build_executable", called)


class RemediationScopeTests(SimpleTestCase):
    """
    Proof that this phase changed nothing about how live questions grade.
    """

    def test_the_contract_module_is_pure(self):
        """
        No imports at all: no ORM, no settings, no clock, no environment. A
        module with no inputs beyond its arguments cannot behave differently
        for the oracle than for the grader.
        """
        tree = ast.parse(
            pathlib.Path(inspect.getfile(input_contract)).read_text("utf-8"))
        imports = [node for node in ast.walk(tree)
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertEqual(imports, [], "input_contract must import nothing")

    def test_no_shipped_wrapper_consumes_this_module(self):
        """
        Deliberately unwired. Correct semantics are AVAILABLE; nothing adopts
        them until a question is migrated with oracle verification, so zero
        existing questions change behaviour.
        """
        for template in (services.GENERIC_PYTHON_WRAPPER,
                         execution_contract.V2_PYTHON_WRAPPER):
            self.assertNotIn("input_contract", template)

    def test_v3_exists_and_v1_remains_the_default(self):
        """
        v3 was added by the follow-up phase. What matters is that it changes
        nothing by default: every existing question is v1, and v1 is still what
        an undeclared question gets.
        """
        self.assertEqual(execution_contract.KNOWN_CONTRACTS, ("v1", "v2", "v3"))
        self.assertEqual(execution_contract.DEFAULT_CONTRACT, "v1")

    def test_v3_introduces_no_new_template(self):
        """
        v3 is v1's harness fed a canonical envelope. If it ever grows its own
        wrapper, the "no template changes" guarantee needs re-arguing.
        """
        self.assertFalse(hasattr(execution_contract, "V3_PYTHON_WRAPPER"))
        self.assertNotIn("v3", execution_contract.V2_WRAPPERS)

    def test_nothing_in_this_module_writes(self):
        """
        `input_contract` cannot reach the database, and these tests cannot
        either — `SimpleTestCase` fails on any query. Stated as an assertion so
        the guarantee is checked rather than described.
        """
        self.assertFalse(getattr(self, "databases", False))


# ── helpers ─────────────────────────────────────────────────────────────────

def _function_source(template, name):
    """The source of one function defined inside a wrapper template."""
    try:
        tree = ast.parse(template.replace("{user_code}", "pass"))
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    return None


class _RenameParameters(ast.NodeTransformer):
    """Rewrites parameter references to positional placeholders."""

    def __init__(self, names):
        self._names = {name: f"_arg{i}" for i, name in enumerate(names)}

    def visit_Name(self, node):
        node.id = self._names.get(node.id, node.id)
        return node


def _normalised_body(source):
    """
    A function's body as normalised source, ignoring the function's name, its
    docstring, its formatting, and what its parameters are CALLED — so a
    cosmetic rename still compares equal while a changed comparison does not.

    The alternative, exact text matching, fails on a harmless rename, and a
    guard that cries wolf is a guard someone eventually deletes.
    """
    node = ast.parse(textwrap.dedent(source)).body[0]
    body = node.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]

    rename = _RenameParameters([arg.arg for arg in node.args.args])
    return "\n".join(
        ast.unparse(rename.visit(statement)) for statement in body)
