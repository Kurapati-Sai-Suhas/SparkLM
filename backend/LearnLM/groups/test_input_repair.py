"""
M18 — the input-repair module and the pilot command that applies it.

Behavioural throughout: every safety test drives the real command (or the real
pure function) and asserts what was or was not WRITTEN, plus the reason given.
The fixtures are synthetic — invented inputs with hand-checked answers — and
never the production questions' hidden tests, which are grading truth.

Local/synthetic database only. Nothing here reaches Judge0.
"""

import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from groups import input_repair as ir
from groups import pre_image
from groups.management.commands import input_repair_pilot as pilot
from groups.management.commands import remediate_inputs
from groups.models import (
    CodingPortal, Question, RemediationAction, RemediationBatch, Topic,
)
from groups.services import GENERIC_JAVA_WRAPPER, GENERIC_JS_WRAPPER, \
    GENERIC_PYTHON_WRAPPER

User = get_user_model()

JAVAC, JAVA, NODE = (shutil.which("javac"), shutil.which("java"),
                     shutil.which("node"))
needs_java = pytest.mark.skipif(not (JAVAC and JAVA), reason="no local JDK")
needs_node = pytest.mark.skipif(not NODE, reason="no local node")

BATCH = "m18-test"

#: Synthetic stand-ins for the three pilot questions. Inputs are invented and
#: the expected outputs hand-checked: [3,8,2,9] -> 7, "abc" needs 2 cuts, ...
SPECS = {
    121: {"title": "Best Time to Buy and Sell Stock", "method": "maxProfit",
          "param": "prices", "py": "list[int]", "java": "int[]",
          "cases": [("3,8,2,9", "7"), ("9,4,1", "0"), ("5,6", "1")],
          "after": ["[[3,8,2,9]]", "[[9,4,1]]", "[[5,6]]"]},
    132: {"title": "Palindrome Partitioning II", "method": "minCut",
          "param": "s", "py": "str", "java": "String",
          "cases": [("'noon'", "0"), ("'abc'", "2"), ("'aabb'", "1")],
          "after": ["noon", "abc", "aabb"]},
    516: {"title": "Longest Palindromic Subsequence",
          "method": "longestPalindromeSubseq", "param": "s", "py": "str",
          "java": "String",
          "cases": [("'abcb'\n", "3"), ("'xyz'\n", "1")],
          "after": ["abcb", "xyz"]},
}


def starters(spec, *, java_method=None):
    method, param = spec["method"], spec["param"]
    return {
        "python": (f"class Solution:\n    def {method}(self, {param}: "
                   f"{spec['py']}) -> int:\n        pass\n"),
        "javascript": (f"var Solution = function() {{}};\n"
                       f"Solution.prototype.{method} = function({param}) {{\n"
                       f"    return 0;\n}};\n"),
        "java": (f"class Solution {{\n    public int {java_method or method}"
                 f"({spec['java']} {param}) {{\n        return 0;\n    }}\n}}\n"),
    }


def suite_of(spec):
    return [{"stdin": stdin, "expected_output": expected}
            for stdin, expected in spec["cases"]]


def unsaved(pk, **overrides):
    """An unsaved question — for the pure module, which only reads."""
    spec = SPECS[pk]
    fields = {"id": pk, "title": spec["title"],
              "content": f"Statement for {spec['title']}.",
              "boilerplate_code": starters(spec),
              "hidden_test_cases": suite_of(spec), "hidden_wrapper_code": {},
              "execution_contract_version": "v1"}
    fields.update(overrides)
    return Question(**fields)


# ═════════════════════════════════════════════════════════════
# Fixtures for the command
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def operator(db):
    return User.objects.create_user(username="m18-op", password="pw",
                                    email="m18@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="M18 Portal")
    made, _ = Topic.objects.get_or_create(
        name="M18Topic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, pk, **overrides):
    spec = SPECS[pk]
    fields = {"id": pk, "title": spec["title"],
              "content": f"Statement for {spec['title']}.", "topic": topic,
              "base_difficulty": 1200.0, "boilerplate_code": starters(spec),
              "hidden_test_cases": suite_of(spec), "hidden_wrapper_code": {},
              "execution_contract_version": "v1"}
    fields.update(overrides)
    return Question.objects.create(**fields)


@pytest.fixture
def questions(db, topic):
    return {pk: make_question(topic, pk) for pk in SPECS}


@pytest.fixture
def control(db, topic):
    """A question outside the pilot. It must never move."""
    return Question.objects.create(
        id=264, title="Control", content="Control.", topic=topic,
        base_difficulty=1200.0, boilerplate_code={},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v1")


def capture(operator, batch_key=BATCH, pks=tuple(SPECS), freeze=True):
    batch = RemediationBatch.objects.create(batch_key=batch_key,
                                            purpose="test", created_by=operator)
    for pk in pks:
        pre_image.capture(batch, Question.objects.get(pk=pk), operator)
    if freeze:
        pre_image.freeze(batch, operator)
    return batch


@pytest.fixture
def frozen(db, operator, questions, control):
    return capture(operator)


def review_for(pk, **overrides):
    """A CONSISTENT review written against the live question, independently."""
    question = Question.objects.get(pk=pk)
    review = {"verdict": "CONSISTENT", "title": question.title.strip(),
              "method": SPECS[pk]["method"],
              "statement_sha256": hashlib.sha256(
                  question.content.encode("utf-8")).hexdigest()}
    review.update(overrides)
    return review


def write_manifest(tmp_path, pks=tuple(SPECS), *, afters=None, reviews=None,
                   batch=BATCH, extra_entries=()):
    """The operator's files: one changes file per question, one manifest."""
    entries = []
    for pk in pks:
        question = Question.objects.filter(pk=pk).first()
        stored = question.hidden_test_cases if question else []
        after = (afters or {}).get(pk, SPECS.get(pk, {}).get("after", []))
        changes = [{"case": index, "before": case["stdin"], "after": new}
                   for index, (case, new) in enumerate(zip(stored, after),
                                                       start=1)]
        name = f"q{pk}_cases_input.json"
        (tmp_path / name).write_text(
            json.dumps({"question": pk, "changes": changes}), encoding="utf-8")
        review = (reviews or {}).get(pk) or (
            review_for(pk) if question and pk in SPECS else {})
        entries.append({"question": pk, "changes_file": name,
                        "review": review})
    entries.extend(extra_entries)
    path = tmp_path / "m18_pilot_manifest.json"
    path.write_text(json.dumps({"batch": batch, "questions": entries}),
                    encoding="utf-8")
    return str(path)


def run(manifest, operator, *extra):
    out = io.StringIO()
    call_command("input_repair_pilot", "--manifest", manifest,
                 "--reason", "M18 test", "--operator", operator.username,
                 "--local", *extra, stdout=out)
    return out.getvalue()


def dry_run_digest(manifest, operator):
    output = run(manifest, operator)
    return re.search(r"PLAN DIGEST\s+([0-9a-f]{64})", output).group(1)


def apply(manifest, operator, digest=None):
    digest = digest or dry_run_digest(manifest, operator)
    return run(manifest, operator, "--apply", "--confirm",
               "--plan-digest", digest)


def snapshot():
    return {q.pk: pre_image.question_state(q) for q in Question.objects.all()}


def refused(manifest, operator, *extra):
    """Run the command expecting a refusal; return the reason."""
    with pytest.raises(CommandError) as raised:
        run(manifest, operator, *extra)
    return str(raised.value)


def assert_nothing_written(before):
    assert snapshot() == before
    assert not RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_INPUT_REPAIR).exists()


# ═════════════════════════════════════════════════════════════
# Decoders — the closed set
# ═════════════════════════════════════════════════════════════

def test_the_json_decoder_reads_a_value_of_the_declared_type():
    assert ir.decode_field("[1,-2,3]", "list[int]") == ([1, -2, 3], ir.JSON_VALUE)
    assert ir.decode_field(" 5 \n", "int") == (5, ir.JSON_VALUE)
    assert ir.decode_field("[]", "list[int]") == ([], ir.JSON_VALUE)


def test_a_json_string_and_its_python_literal_reading_agree():
    assert ir.decode_field('"ab"', "str") == ("ab", ir.JSON_VALUE)


@pytest.mark.parametrize("text, declared", [
    ("true", "int"), ("1.5", "int"), ('[1,"2"]', "list[int]"),
    ("[true]", "list[int]"), ("NaN", "int"), ("[[1]]", "list[int]"),
])
def test_the_json_decoder_refuses_the_wrong_type(text, declared):
    with pytest.raises(ir.RepairRefused):
        ir.decode_field(text, declared)


def test_the_python_string_literal_decoder():
    assert ir.decode_field("'qrs'", "str") == ("qrs", ir.PYTHON_STRING_LITERAL)
    assert ir.decode_field("'a b'\n", "str") == ("a b", ir.PYTHON_STRING_LITERAL)
    assert ir.decode_field("'it\\'s'", "str") == ("it's",
                                                   ir.PYTHON_STRING_LITERAL)


@pytest.mark.parametrize("text", [
    "'a' 'b'",            # implicit concatenation is two literals
    "'''abc'''",          # triple quotes
    "r'abc'", "b'abc'", "f'abc'",
    "'unterminated",
    "qrs",                # bare text: no rule reads it, so nothing guesses
    "('a',)",             # a tuple, not a string
])
def test_the_python_string_literal_decoder_refuses_everything_else(text):
    with pytest.raises(ir.RepairRefused):
        ir.decode_field(text, "str")


def test_the_comma_separated_integer_decoder():
    assert ir.decode_field("4,2,-6", "list[int]") == (
        [4, 2, -6], ir.COMMA_SEPARATED_INTEGERS)


@pytest.mark.parametrize("text", ["4, 2", "4,,2", "7,", ",7", "1.5,2", "7",
                                  "a,b", "7;1"])
def test_the_comma_separated_integer_decoder_is_exact(text):
    with pytest.raises(ir.RepairRefused):
        ir.decode_field(text, "list[int]")


def test_an_unsupported_declaration_is_refused():
    with pytest.raises(ir.RepairRefused, match="not one this repair supports"):
        ir.decode_field("1.5", "float")


# ═════════════════════════════════════════════════════════════
# The canonical v1 encoding
# ═════════════════════════════════════════════════════════════

def test_a_list_is_wrapped_so_v1_does_not_splat_it():
    assert ir.canonical_stdin([4, 2, 6], "list[int]") == "[[4,2,6]]"
    assert ir.canonical_stdin([], "list[int]") == "[[]]"


def test_an_int_is_its_json_number():
    assert ir.canonical_stdin(-5, "int") == "-5"


@pytest.mark.parametrize("value, text", [
    ("qrs", "qrs"), ("a b", "a b"),
    (" ab", '" ab"'), ("ab ", '"ab "'),          # whitespace the harness strips
    ("123", '"123"'), ("true", '"true"'),        # text JSON would retype
    ("NaN", '"NaN"'), ("null", '"null"'),
    ("'q'", "\"'q'\""), ("[x]", '"[x]"'),         # a literal a validator reads
])
def test_a_string_is_bare_only_when_bare_text_is_exact(value, text):
    assert ir.canonical_stdin(value, "str") == text


@pytest.mark.parametrize("edge", [0xFEFF, 0x200B, 0x00A0])
def test_an_invisible_edge_character_is_never_left_bare(edge):
    # JavaScript's trim() strips a byte-order mark that Python's strip() keeps.
    value = chr(edge) + "ab"
    assert ir.canonical_stdin(value, "str") == json.dumps(value,
                                                          ensure_ascii=False)


def test_a_string_the_seam_would_split_is_refused():
    # json.dumps("a\nb") holds a literal backslash-n, which prepare_stdin
    # expands into a line break before any harness reads it.
    with pytest.raises(ir.RepairRefused, match="literal"):
        ir.canonical_stdin("a\nb", "str")


# ═════════════════════════════════════════════════════════════
# Binding — what each v1 harness hands the method
# ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("stdin, expected", [
    ("[[4,2]]", [[4, 2]]),                 # one list argument
    ("[4,2]", [4, 2]),                     # splatted: two arguments
    ("4,2", ["4,2"]),                      # raw text
    ("'qrs'", ["'qrs'"]),                  # quotes and all
    ("qrs", ["qrs"]),
    ('{"prices":[1]}', [[1]]),             # keyword in Python
    ('{"other":[1]}', None),               # a keyword the method lacks
])
def test_python_binding(stdin, expected):
    assert ir.reflection_values(stdin, "python", "prices") == expected


@pytest.mark.parametrize("stdin, expected", [
    ("[[4,2]]", [[4, 2]]),
    ("4,2", ["4,2"]),
    ('{"prices":[1]}', [{"prices": [1]}]),  # ONE positional object in JS
    ("NaN", ["NaN"]),                       # JSON.parse rejects NaN
])
def test_javascript_binding(stdin, expected):
    assert ir.reflection_values(stdin, "javascript", "prices") == expected


def test_python_reads_nan_as_a_float_where_javascript_reads_text():
    (value,) = ir.reflection_values("NaN", "python", "x")
    assert isinstance(value, float) and value != value


@pytest.mark.parametrize("stdin, types, expected", [
    ("4,2,6", ("int[]",), [[4, 2, 6]]),
    ("[[4,2,6]]", ("int[]",), [[4, 2, 6]]),
    ("[4, 2 ,6]", ("int[]",), [[4, 2, 6]]),
    ("[]", ("int[]",), [[]]),
    ("[[]]", ("int[]",), [[]]),
    ("'qrs'", ("String",), ["'qrs'"]),
    ('"qrs"', ("String",), ['"qrs"']),
    ("  qrs \n", ("String",), ["qrs"]),
    ("42", ("int",), [42]),
    ("+7", ("int",), [7]),
])
def test_java_binding_mirror(stdin, types, expected):
    assert ir.java_values(stdin, types) == expected


@pytest.mark.parametrize("stdin, types", [
    ("4.2", ("int",)), ("2147483648", ("int",)), ("1,x", ("int[]",)),
    (",1", ("int[]",)), ("a\r\nb", ("String",)), ("5", ("int", "int")),
    ("x", ("long",)),
])
def test_java_binding_mirror_declines_what_java_would_throw_on(stdin, types):
    assert ir.java_values(stdin, types) is None


def test_type_strict_equality():
    assert ir.same_values([[1, 2]], [[1, 2]])
    assert not ir.same_values([True], [1])
    assert not ir.same_values([1.0], [1])
    assert not ir.same_values(None, [1])


# ── the mirrors against the REAL templates ──────────────────────────────────

def _run(cmd, source_name, source, stdin, cwd):
    path = Path(cwd) / source_name
    path.write_text(source, encoding="utf-8")
    return subprocess.run(cmd + [str(path)], input=stdin.encode("utf-8"),
                          capture_output=True, timeout=60)


ECHO_PYTHON = ("import json\nclass Solution:\n    def echo(self, *args, **kw):\n"
               "        return json.dumps([list(args), kw])\n")


@pytest.mark.parametrize("stdin", ["[[4,2]]", "4,2", "'qrs'", "qrs",
                                   '{"prices":[1]}'])
def test_the_python_binding_agrees_with_the_real_wrapper(stdin, tmp_path):
    source = GENERIC_PYTHON_WRAPPER.replace("{user_code}", ECHO_PYTHON)
    run = _run([sys.executable], "probe.py", source, stdin, tmp_path)
    args, kwargs = json.loads(run.stdout.decode().strip())
    actual = [kwargs["prices"]] if kwargs else args
    assert actual == ir.reflection_values(stdin, "python", "prices")


ECHO_JS = ("class Solution {\n  echo(...args) {\n"
           "    return JSON.stringify(args);\n  }\n}\n")


@needs_node
@pytest.mark.parametrize("stdin", ["[[4,2]]", "4,2", "'qrs'", "qrs",
                                   '{"prices":[1]}'])
def test_the_javascript_binding_agrees_with_the_real_wrapper(stdin, tmp_path):
    source = GENERIC_JS_WRAPPER.replace("{user_code}", ECHO_JS)
    run = _run([NODE], "probe.js", source, stdin, tmp_path)
    assert json.loads(run.stdout.decode().strip()) == ir.reflection_values(
        stdin, "javascript", "prices")


JAVA_ECHO = {
    "int[]": ("int[] a", 'java.util.Arrays.toString(a).replace(" ", "")'),
    "String": ("String a", '"<" + a + ">"'),
    "int": ("int a", '"#" + a'),
}
JAVA_CASES = [("4,2,6", "int[]"), ("[[4,2,6]]", "int[]"), ("[4, 2 ,6]", "int[]"),
              ("[]", "int[]"), ("1,x", "int[]"), ("'qrs'", "String"),
              ('"qrs"', "String"), ("  qrs \n", "String"), ("42", "int"),
              ("4.2", "int"), ("2147483648", "int")]


@pytest.fixture(scope="module")
def java_probes():
    built = {}
    if not (JAVAC and JAVA):
        return built
    for java_type, (parameter, render) in JAVA_ECHO.items():
        workdir = tempfile.mkdtemp()
        source = (f"class Solution {{\n  public String echo({parameter}) {{\n"
                  f"    return {render};\n  }}\n}}\n")
        Path(workdir, "Main.java").write_text(
            GENERIC_JAVA_WRAPPER.replace("{user_code}", source),
            encoding="utf-8")
        subprocess.run([JAVAC, "Main.java"], cwd=workdir, check=True,
                       capture_output=True, timeout=180)
        built[java_type] = workdir
    return built


@needs_java
@pytest.mark.parametrize("stdin, java_type", JAVA_CASES)
def test_the_java_mirror_agrees_with_the_real_wrapper(stdin, java_type,
                                                      java_probes):
    run = subprocess.run([JAVA, "-cp", java_probes[java_type], "Main"],
                         input=stdin.encode("utf-8"), capture_output=True,
                         timeout=60)
    mirrored = ir.java_values(stdin, (java_type,))
    if mirrored is None:
        assert run.returncode != 0
        return
    assert run.returncode == 0
    (value,) = mirrored
    expected = {"int[]": json.dumps(value, separators=(",", ":")),
                "String": f"<{value}>", "int": f"#{value}"}[java_type]
    assert run.stdout.decode().strip() == expected


# ═════════════════════════════════════════════════════════════
# The plan
# ═════════════════════════════════════════════════════════════

def test_the_three_pilot_shapes_plan_to_the_canonical_text():
    for pk, spec in SPECS.items():
        repair = ir.plan(unsaved(pk))
        assert [c.after for c in repair.cases] == spec["after"]
        assert repair.languages == ("python", "javascript", "java")


def test_the_plan_records_what_each_language_receives_today():
    repair = ir.plan(unsaved(121))
    assert dict(repair.cases[0].bound_before) == {
        "python": False, "javascript": False, "java": True}


def test_a_v3_question_is_refused():
    with pytest.raises(ir.RepairRefused, match="contract v3"):
        ir.plan(unsaved(121, execution_contract_version="v3"))


def test_a_question_with_its_own_wrapper_is_refused():
    with pytest.raises(ir.RepairRefused, match="own wrapper"):
        ir.plan(unsaved(121, hidden_wrapper_code={"python": "{user_code}"}))


def test_a_two_parameter_method_is_refused():
    starter = {"python": "class Solution:\n    def f(self, a: int, b: int) "
                         "-> int:\n        pass\n"}
    with pytest.raises(ir.RepairRefused, match="exactly one"):
        ir.plan(unsaved(121, boilerplate_code=starter))


def test_an_unsupported_declaration_is_refused_by_the_plan():
    starter = {"python": "class Solution:\n    def f(self, a: list[str]) "
                         "-> int:\n        pass\n"}
    with pytest.raises(ir.RepairRefused, match="does not support"):
        ir.plan(unsaved(121, boilerplate_code=starter))


def test_a_method_graded_on_side_effects_is_refused():
    starter = {"python": "class Solution:\n    def f(self, a: list[int]) "
                         "-> None:\n        pass\n"}
    with pytest.raises(ir.RepairRefused, match="returns nothing"):
        ir.plan(unsaved(121, boilerplate_code=starter))


def test_a_served_self_contained_language_is_refused():
    code = dict(starters(SPECS[121]),
                cpp="#include <cstdio>\nint main() { return 0; }\n")
    with pytest.raises(ir.RepairRefused, match="reads stdin itself"):
        ir.plan(unsaved(121, boilerplate_code=code))


def test_starters_that_disagree_on_the_method_are_refused():
    code = starters(SPECS[121], java_method="maxGain")
    with pytest.raises(ir.RepairRefused, match="disagree on the method"):
        ir.plan(unsaved(121, boilerplate_code=code))


def test_a_value_java_would_receive_quoted_is_refused():
    # " ab" must be JSON-quoted for Python and JavaScript, and Java would then
    # receive the quotes: no representation reaches all three.
    suite = [{"stdin": "' ab'", "expected_output": "1"}]
    with pytest.raises(ir.RepairRefused, match="java would not receive"):
        ir.plan(unsaved(132, hidden_test_cases=suite))


# ═════════════════════════════════════════════════════════════
# The pure checks a write is held to
# ═════════════════════════════════════════════════════════════

def repaired(pk):
    question = unsaved(pk)
    return question, ir.plan(question), question.hidden_test_cases


def proposal(pk):
    return [dict(case, stdin=after) for case, after
            in zip(suite_of(SPECS[pk]), SPECS[pk]["after"])]


def test_the_transition_check_accepts_exactly_the_plan():
    question, repair, before = repaired(121)
    ir.check_suite_transition(question, repair, before, proposal(121))


def test_the_transition_check_refuses_a_changed_expected_output():
    question, repair, before = repaired(121)
    after = proposal(121)
    after[0]["expected_output"] = "8"
    with pytest.raises(ir.RepairRefused, match="expected output changed"):
        ir.check_suite_transition(question, repair, before, after)


def test_the_transition_check_refuses_reordered_cases():
    question, repair, before = repaired(121)
    after = proposal(121)
    after[0]["stdin"], after[1]["stdin"] = after[1]["stdin"], after[0]["stdin"]
    with pytest.raises(ir.RepairRefused, match="cases moved"):
        ir.check_suite_transition(question, repair, before, after)


def test_the_transition_check_refuses_a_dropped_case():
    question, repair, before = repaired(121)
    with pytest.raises(ir.RepairRefused, match="case count"):
        ir.check_suite_transition(question, repair, before, proposal(121)[:2])


def test_the_transition_check_refuses_another_key_changing():
    question, repair, before = repaired(121)
    after = proposal(121)
    after[0]["category"] = "edge"
    with pytest.raises(ir.RepairRefused, match="other than stdin"):
        ir.check_suite_transition(question, repair, before, after)


def test_the_transition_check_is_type_strict_about_other_keys():
    # Python's == calls 1 and True equal; the stored JSON does not.
    question = unsaved(121, hidden_test_cases=[
        dict(case, weight=1) for case in suite_of(SPECS[121])])
    repair = ir.plan(question)
    after = [dict(case, weight=1) for case in proposal(121)]
    ir.check_suite_transition(question, repair, question.hidden_test_cases,
                              after)
    after[0]["weight"] = True
    with pytest.raises(ir.RepairRefused, match="other than stdin"):
        ir.check_suite_transition(question, repair,
                                  question.hidden_test_cases, after)


def changes_for(pk, afters=None):
    return [{"case": index, "before": stdin, "after": after}
            for index, ((stdin, _), after) in enumerate(
                zip(SPECS[pk]["cases"], afters or SPECS[pk]["after"]),
                start=1)]


def test_the_changes_check_accepts_exactly_the_plan():
    question, repair, _ = repaired(121)
    ir.check_changes(question, repair, changes_for(121))


def test_the_changes_check_refuses_a_value_moved_between_cases():
    question, repair, _ = repaired(121)
    afters = list(SPECS[121]["after"])
    afters[0], afters[1] = afters[1], afters[0]
    with pytest.raises(ir.RepairRefused, match="value mismatch"):
        ir.check_changes(question, repair, changes_for(121, afters))


def test_the_changes_check_refuses_a_language_regression():
    # Python binds the keyword form correctly; Java, which receives the
    # intended array TODAY, would throw on it.
    question, repair, _ = repaired(121)
    afters = ['{"prices":[3,8,2,9]}'] + SPECS[121]["after"][1:]
    with pytest.raises(ir.RepairRefused, match="language regression in java"):
        ir.check_changes(question, repair, changes_for(121, afters))


def test_the_changes_check_refuses_an_equivalent_but_non_canonical_text():
    question, repair, _ = repaired(121)
    afters = ["[[3, 8, 2, 9]]"] + SPECS[121]["after"][1:]
    with pytest.raises(ir.RepairRefused, match="not the canonical"):
        ir.check_changes(question, repair, changes_for(121, afters))


def test_the_changes_check_refuses_a_stale_before():
    question, repair, _ = repaired(121)
    changes = changes_for(121)
    changes[0]["before"] = "3,8,2"
    with pytest.raises(ir.RepairRefused, match="written against"):
        ir.check_changes(question, repair, changes)


def test_the_changes_check_refuses_a_missing_case():
    question, repair, _ = repaired(121)
    with pytest.raises(ir.RepairRefused, match="names cases"):
        ir.check_changes(question, repair, changes_for(121)[:2])


def fingerprint(pk):
    question = unsaved(pk)
    return question, ir.plan(question), {
        "verdict": "CONSISTENT", "title": question.title,
        "method": SPECS[pk]["method"],
        "statement_sha256": hashlib.sha256(
            question.content.encode()).hexdigest()}


def test_a_consistent_review_against_the_live_question_passes():
    question, repair, review = fingerprint(132)
    ir.check_review(question, repair, review)


@pytest.mark.parametrize("key, value, match", [
    ("verdict", "MISMATCH", "only a question reviewed CONSISTENT"),
    ("title", "Reconstruct Original Digits", "attests title"),
    ("method", "sortWordsByNumbers", "attests method"),
    ("statement_sha256", "0" * 64, "attests statement"),
])
def test_a_review_that_does_not_fit_is_refused(key, value, match):
    question, repair, review = fingerprint(132)
    review[key] = value
    with pytest.raises(ir.RepairRefused, match=match):
        ir.check_review(question, repair, review)


# ═════════════════════════════════════════════════════════════
# The command — the workflow
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_dry_run_writes_nothing_and_prints_a_digest(frozen, operator,
                                                      tmp_path):
    before = snapshot()
    output = run(write_manifest(tmp_path), operator)
    assert re.search(r"PLAN DIGEST\s+[0-9a-f]{64}", output)
    assert "DRY RUN" in output
    assert_nothing_written(before)


@pytest.mark.django_db
def test_apply_repairs_exactly_the_planned_stdin(frozen, operator, tmp_path):
    before = snapshot()
    apply(write_manifest(tmp_path), operator)

    after = snapshot()
    for pk, spec in SPECS.items():
        was, now = before[pk], after[pk]
        assert [c["stdin"] for c in now["hidden_test_cases"]] == spec["after"]
        assert [c["expected_output"] for c in now["hidden_test_cases"]] == \
            [c["expected_output"] for c in was["hidden_test_cases"]]
        for name in pre_image.CAPTURED_FIELDS:
            if name != "hidden_test_cases":
                assert now[name] == was[name], (pk, name)
        question = Question.objects.get(pk=pk)
        assert question.execution_contract_version == "v1"
        assert question.trust_state == Question.TRUST_UNVERIFIED
        assert not question.is_adaptive_eligible
    assert after[264] == before[264]


@pytest.mark.django_db
def test_one_input_repair_audit_row_per_question(frozen, operator, tmp_path):
    manifest = write_manifest(tmp_path)
    digest = dry_run_digest(manifest, operator)
    apply(manifest, operator, digest)

    actions = RemediationAction.objects.filter(batch=frozen).order_by("id")
    assert [a.question_id for a in actions] == [121, 132, 516]
    for action in actions:
        assert action.action_class == RemediationAction.CLASS_INPUT_REPAIR
        assert action.applied_by == operator
        assert digest in action.detail
        assert action.post_digest == pre_image.live_digest(action.question)


@pytest.mark.django_db
def test_the_pre_images_still_hold_the_original_suites(frozen, operator,
                                                       tmp_path):
    apply(write_manifest(tmp_path), operator)
    for record in frozen.pre_images.all():
        pre_image.verify(record)
        assert record.hidden_test_cases == suite_of(SPECS[record.question_id])


@pytest.mark.django_db
def test_rollback_dry_run_then_rollback_restores_the_originals(
        frozen, operator, tmp_path):
    original = snapshot()
    apply(write_manifest(tmp_path), operator)

    repaired_state = snapshot()
    call_command("preimage_rollback", "--batch", BATCH, "--operator",
                 operator.username, "--local", stdout=io.StringIO())
    assert snapshot() == repaired_state           # the dry run wrote nothing

    call_command("preimage_rollback", "--batch", BATCH, "--operator",
                 operator.username, "--local", "--apply", "--confirm",
                 stdout=io.StringIO())
    assert snapshot() == original


# ═════════════════════════════════════════════════════════════
# The command — selection
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_the_pilot_questions_are_accepted(frozen, operator, tmp_path):
    manifest = write_manifest(tmp_path, pks=(121, 264))
    assert "not a pilot question" in refused(manifest, operator)


@pytest.mark.parametrize("pk", sorted(pilot.PROTECTED_QUESTIONS))
@pytest.mark.django_db
def test_protected_questions_are_refused_by_name(frozen, operator, tmp_path,
                                                 pk):
    entry = {"question": pk, "changes_file": "x.json", "review": {}}
    manifest = write_manifest(tmp_path, pks=(121,), extra_entries=[entry])
    assert f"question {pk} is protected" in refused(manifest, operator)


@pytest.mark.django_db
def test_at_most_three_questions_per_pilot(frozen, operator, tmp_path):
    entry = {"question": 132, "changes_file": "x.json", "review": {}}
    manifest = write_manifest(tmp_path, extra_entries=[entry])
    assert "at most 3" in refused(manifest, operator)


@pytest.mark.django_db
def test_a_question_named_twice_is_refused(frozen, operator, tmp_path):
    entry = {"question": 121, "changes_file": "q121_cases_input.json",
             "review": {}}
    manifest = write_manifest(tmp_path, pks=(121, 132), extra_entries=[entry])
    assert "named twice" in refused(manifest, operator)


# ═════════════════════════════════════════════════════════════
# The command — the ten safety cases
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_s1_no_frozen_pre_image_refuses_the_dry_run(db, operator, questions,
                                                    control, tmp_path):
    capture(operator, freeze=False)
    before = snapshot()
    assert "is not frozen" in refused(write_manifest(tmp_path), operator)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s1_a_question_missing_from_the_batch_is_refused(
        db, operator, questions, control, tmp_path):
    capture(operator, pks=(121, 132))
    before = snapshot()
    assert "has no pre-image" in refused(write_manifest(tmp_path), operator)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s2_an_input_changed_after_the_freeze_is_refused(frozen, operator,
                                                         tmp_path):
    question = Question.objects.get(pk=132)
    suite = question.hidden_test_cases
    suite[0]["stdin"] = '"noon"'           # still repairable, but not frozen
    question.hidden_test_cases = suite
    question.save(update_fields=["hidden_test_cases"])

    before = snapshot()
    reason = refused(write_manifest(tmp_path), operator)
    assert "moved since its pre-image was frozen" in reason
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s3_an_expected_output_changed_before_the_write_is_refused(
        frozen, operator, tmp_path, monkeypatch):
    original = remediate_inputs.Command._derive

    def derive_that_edits_an_answer(self, current, changes):
        proposed = original(self, current, changes)
        proposed[0]["expected_output"] = "999"
        return proposed

    monkeypatch.setattr(remediate_inputs.Command, "_derive",
                        derive_that_edits_an_answer)
    before = snapshot()
    assert "expected output changed" in refused(write_manifest(tmp_path),
                                                operator)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s4_a_question_outside_the_input_repair_class_is_refused(
        db, operator, topic, control, tmp_path):
    make_question(topic, 121)
    make_question(topic, 516)
    # JSON strings: Python already receives the intended value, so this is not
    # a v1 input defect, whatever Java receives.
    make_question(topic, 132, hidden_test_cases=[
        {"stdin": '"noon"', "expected_output": "0"},
        {"stdin": '"abc"', "expected_output": "2"}])
    capture(operator)
    before = snapshot()
    manifest = write_manifest(tmp_path, afters={132: ["noon", "abc"]})
    assert "not an input-repair candidate" in refused(manifest, operator)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s4_an_answer_form_defect_riding_along_is_refused(
        db, operator, topic, control, tmp_path):
    make_question(topic, 132)
    make_question(topic, 516)
    # A non-integer answer for an int method: still refused once the input is
    # repaired, so it belongs to a different action class.
    make_question(topic, 121, hidden_test_cases=[
        {"stdin": "3,8,2,9", "expected_output": "seven"},
        {"stdin": "9,4,1", "expected_output": "0"},
        {"stdin": "5,6", "expected_output": "1"}])
    capture(operator)
    before = snapshot()
    assert "does not own" in refused(write_manifest(tmp_path), operator)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s5_a_title_signature_mismatch_is_refused(frozen, operator, tmp_path):
    reviews = {516: review_for(516, verdict="MISMATCH")}
    before = snapshot()
    reason = refused(write_manifest(tmp_path, reviews=reviews), operator)
    assert "only a question reviewed CONSISTENT" in reason
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s5_a_review_of_a_different_title_is_refused(frozen, operator,
                                                     tmp_path):
    reviews = {132: review_for(132, title="Reconstruct Original Digits")}
    before = snapshot()
    assert "attests title" in refused(
        write_manifest(tmp_path, reviews=reviews), operator)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s6_an_accidental_contract_change_rolls_everything_back(
        frozen, operator, tmp_path, monkeypatch):
    def write_that_also_migrates(self, alias, locked, proposed):
        locked.hidden_test_cases = proposed
        locked.execution_contract_version = "v2"
        locked.save(using=alias, update_fields=["hidden_test_cases",
                                                "execution_contract_version"])

    manifest = write_manifest(tmp_path)
    digest = dry_run_digest(manifest, operator)
    monkeypatch.setattr(pilot.Command, "_write", write_that_also_migrates)
    before = snapshot()
    with pytest.raises(CommandError, match="execution_contract_version changed"):
        apply(manifest, operator, digest)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s6_a_question_already_off_v1_is_refused(db, operator, topic, control,
                                                 tmp_path):
    make_question(topic, 121)
    make_question(topic, 132)
    make_question(topic, 516, execution_contract_version="v3")
    capture(operator)
    before = snapshot()
    assert "contract v3" in refused(write_manifest(tmp_path), operator)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s7_reordered_cases_are_refused(frozen, operator, tmp_path):
    afters = list(SPECS[121]["after"])
    afters[0], afters[1] = afters[1], afters[0]
    before = snapshot()
    reason = refused(write_manifest(tmp_path, afters={121: afters}), operator)
    assert "value mismatch" in reason
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s8_an_expected_output_changed_in_the_write_rolls_back(
        frozen, operator, tmp_path, monkeypatch):
    def write_that_edits_an_answer(self, alias, locked, proposed):
        edited = [dict(case) for case in proposed]
        edited[-1]["expected_output"] = "999"
        locked.hidden_test_cases = edited
        locked.save(using=alias, update_fields=["hidden_test_cases"])

    manifest = write_manifest(tmp_path)
    digest = dry_run_digest(manifest, operator)
    monkeypatch.setattr(pilot.Command, "_write", write_that_edits_an_answer)
    before = snapshot()
    with pytest.raises(CommandError, match="expected output changed"):
        apply(manifest, operator, digest)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s9_a_language_regression_is_refused(frozen, operator, tmp_path):
    afters = ['{"prices":[3,8,2,9]}'] + SPECS[121]["after"][1:]
    before = snapshot()
    reason = refused(write_manifest(tmp_path, afters={121: afters}), operator)
    assert "language regression in java" in reason
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s10_a_failure_on_the_last_question_leaves_none_applied(
        frozen, operator, tmp_path, monkeypatch):
    original = pilot.Command._write

    def write_that_fails_on_516(self, alias, locked, proposed):
        if locked.pk == 516:
            raise RuntimeError("simulated failure on the third question")
        original(self, alias, locked, proposed)

    manifest = write_manifest(tmp_path)
    digest = dry_run_digest(manifest, operator)
    monkeypatch.setattr(pilot.Command, "_write", write_that_fails_on_516)
    before = snapshot()
    with pytest.raises(RuntimeError, match="third question"):
        apply(manifest, operator, digest)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_s10_an_invalid_question_stops_the_pilot_before_any_write(
        frozen, operator, tmp_path):
    afters = {516: ["abcb", "'xyz'"]}          # the last question's file is bad
    before = snapshot()
    refused(write_manifest(tmp_path, afters=afters), operator,
            "--apply", "--confirm", "--plan-digest", "0" * 64)
    assert_nothing_written(before)


# ═════════════════════════════════════════════════════════════
# The command — the digest and stale state
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_apply_requires_the_dry_run_digest(frozen, operator, tmp_path):
    manifest = write_manifest(tmp_path)
    before = snapshot()
    reason = refused(manifest, operator, "--apply", "--confirm",
                     "--plan-digest", "0" * 64)
    assert "is not this plan's digest" in reason
    assert_nothing_written(before)


@pytest.mark.django_db
def test_apply_without_a_digest_is_refused(frozen, operator, tmp_path):
    before = snapshot()
    assert "is not this plan's digest" in refused(
        write_manifest(tmp_path), operator, "--apply", "--confirm")
    assert_nothing_written(before)


@pytest.mark.django_db
def test_a_change_after_the_dry_run_invalidates_its_digest(frozen, operator,
                                                           tmp_path):
    manifest = write_manifest(tmp_path)
    digest = dry_run_digest(manifest, operator)
    Question.objects.filter(pk=132).update(content="Edited after review.")
    before = snapshot()
    with pytest.raises(CommandError):
        apply(manifest, operator, digest)
    assert_nothing_written(before)


@pytest.mark.django_db
def test_a_change_under_the_lock_is_refused(frozen, operator, tmp_path,
                                            monkeypatch):
    """The dry run and the apply agree, then the row moves before the write."""
    manifest = write_manifest(tmp_path)
    digest = dry_run_digest(manifest, operator)
    original = pilot.Command._apply_one

    def move_q132_first(self, alias, batch, op, item, reason, plan):
        if item.question.pk == 132:
            Question.objects.filter(pk=132).update(content="Moved.")
        return original(self, alias, batch, op, item, reason, plan)

    monkeypatch.setattr(pilot.Command, "_apply_one", move_q132_first)
    with pytest.raises(CommandError, match="changed between the dry run"):
        apply(manifest, operator, digest)
    assert not RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_INPUT_REPAIR).exists()
    assert Question.objects.get(pk=121).hidden_test_cases == suite_of(SPECS[121])
