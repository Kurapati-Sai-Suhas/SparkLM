"""
The execution contract (M2 P2.6).

The audit found three reflection harnesses disagreeing in ways that produce
wrong verdicts for correct code: method selection differed per language and was
undefined in Java; Python/JS emitted `[0,1]` where Java emitted `0 1`;
Python/JS parsed JSON while the seeded data is space-separated; and all three
swallowed runtime errors into stdout with exit 0, so a crash was graded as
Wrong Answer.

Two properties dominate this suite.

**v1 must be untouched.** Every existing question defaults to v1, so if v1
changes, real learners' grading changes with it. The v1 tests below assert the
harness still contains the exact constructs that shipped.

**v2 is EXECUTED, not asserted about.** The Python and JavaScript harnesses are
run as real subprocesses with real stdin, because a test that only string-
matches a wrapper proves the template is spelled a certain way, not that it
grades correctly. Java has no local JVM (`java` is not on PATH in this
environment), so its harness gets structural assertions and is explicitly
marked as needing Judge0 verification before any v2 Java question is published.
"""

import subprocess
import sys
import textwrap

import pytest

from groups import execution_contract
from groups.execution_contract import (
    CONTRACT_V1, CONTRACT_V2, UnknownExecutionContract, contract_version,
)
from groups.models import CodingPortal, Question, Topic
from groups.services import GradingService


# ─────────────────────────────────────────────────────────────
# Harness execution helpers
# ─────────────────────────────────────────────────────────────

def run_python(user_code, stdin):
    """Execute the v2 Python harness for real. Returns (stdout, stderr, rc)."""
    source = execution_contract.V2_PYTHON_WRAPPER.replace("{user_code}", user_code)
    proc = subprocess.run(
        [sys.executable, "-c", source], input=stdin, capture_output=True,
        text=True, timeout=30,
    )
    return proc.stdout.strip(), proc.stderr, proc.returncode


def run_js(user_code, stdin):
    """Execute the v2 JavaScript harness for real."""
    source = execution_contract.V2_JS_WRAPPER.replace("{user_code}", user_code)
    proc = subprocess.run(
        ["node", "-e", source], input=stdin, capture_output=True,
        text=True, timeout=30, shell=False,
    )
    return proc.stdout.strip(), proc.stderr, proc.returncode


PY_TWO_SUM = textwrap.dedent("""
    class Solution:
        def twoSum(self, nums: list, target: int):
            seen = {}
            for i, n in enumerate(nums):
                if target - n in seen:
                    return [seen[target - n], i]
                seen[n] = i
            return []
""")

JS_TWO_SUM = textwrap.dedent("""
    class Solution {
        twoSum(nums, target) {
            const seen = new Map();
            for (let i = 0; i < nums.length; i += 1) {
                if (seen.has(target - nums[i])) return [seen.get(target - nums[i]), i];
                seen.set(nums[i], i);
            }
            return [];
        }
    }
""")


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="Contract Portal")
    topic, _ = Topic.objects.get_or_create(
        name="ContractTopic", defaults={"structure_type": "flat", "portal": portal}
    )
    return Question.objects.create(
        title="Contract Problem", content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={},
    )


# ─────────────────────────────────────────────────────────────
# v1 compatibility — the promise this migration rests on
# ─────────────────────────────────────────────────────────────

def test_existing_questions_default_to_v1(question):
    """
    The safety property of the whole phase. Every row in production predates
    versioning; if the default were anything else, this migration would
    silently re-grade the entire question bank.
    """
    assert question.execution_contract_version == "v1"
    assert contract_version(question) == CONTRACT_V1


def test_a_blank_version_is_treated_as_v1(question):
    """Defensive: a row written by a fixture or an old import path."""
    Question.objects.filter(pk=question.pk).update(execution_contract_version="")
    question.refresh_from_db()

    assert contract_version(question) == CONTRACT_V1


def test_an_unknown_version_is_refused_rather_than_defaulted(question):
    """
    Falling back to a default here would grade a question under a contract it
    was not written for — silently, and with no way to notice.
    """
    Question.objects.filter(pk=question.pk).update(execution_contract_version="v9")
    question.refresh_from_db()

    with pytest.raises(UnknownExecutionContract, match="v9"):
        contract_version(question)


def test_v1_still_selects_the_harness_that_shipped(question):
    """
    Pins the legacy behaviour by its distinguishing constructs: JSON output and
    alphabetical method selection. If these disappear, v1 questions are being
    graded by something other than what they were written against.
    """
    executable, _ = GradingService._build_executable(question, "python", "class Solution: pass")

    assert "json.dumps(res)" in executable
    assert "dir(sol)" in executable
    assert "_sparklm_main" not in executable


def test_v2_selects_the_canonical_harness(question):
    Question.objects.filter(pk=question.pk).update(execution_contract_version="v2")
    question.refresh_from_db()

    executable, _ = GradingService._build_executable(question, "python", "class Solution: pass")

    assert "_sparklm_main" in executable
    assert "json.dumps(res)" not in executable


def test_a_custom_wrapper_outranks_the_contract_version(question):
    """
    A per-question wrapper defines its own I/O — seed_problems.py's questions
    are comma-separated. Versioning must never override that, or those
    questions would be graded against a convention they do not use.
    """
    Question.objects.filter(pk=question.pk).update(
        execution_contract_version="v2",
        hidden_wrapper_code={"python": "CUSTOM:{user_code}"},
    )
    question.refresh_from_db()

    executable, _ = GradingService._build_executable(question, "python", "X")

    assert executable == "CUSTOM:X"


def test_c_and_cpp_stay_self_contained_under_v2(question):
    """The learner writes a complete program; there is nothing to wrap."""
    Question.objects.filter(pk=question.pk).update(execution_contract_version="v2")
    question.refresh_from_db()

    for language in ("c", "cpp"):
        executable, stored = GradingService._build_executable(
            question, language, "int main(){return 0;}"
        )
        assert executable == "int main(){return 0;}"
        assert stored == "int main(){return 0;}"


# ─────────────────────────────────────────────────────────────
# v2 Python — executed for real
# ─────────────────────────────────────────────────────────────

def test_python_reads_one_line_per_parameter():
    """
    The seeded format: `"2 7 11 15\\n9"`. Under v1 this failed json.loads, the
    whole blob went in as ONE argument to a two-parameter method, and the
    TypeError was printed to stdout and graded Wrong Answer.
    """
    out, err, rc = run_python(PY_TWO_SUM, "2 7 11 15\n9")

    assert rc == 0, err
    assert out == "0 1"


def test_python_emits_space_separated_output_not_json():
    out, _, _ = run_python(PY_TWO_SUM, "3 2 4\n6")

    assert out == "1 2"
    assert "[" not in out


def test_python_helper_methods_do_not_hijack_grading():
    """
    The defect that motivated the single-method rule: `dir()` is alphabetical,
    so `helper` outranked `twoSum` and correct code was graded as wrong.
    Private helpers are now invisible to selection.
    """
    code = textwrap.dedent("""
        class Solution:
            def _helper(self, x):
                return x * 2
            def twoSum(self, nums: list, target: int):
                return [self._helper(1), 3]
    """)

    out, err, rc = run_python(code, "1 2\n3")

    assert rc == 0, err
    assert out == "2 3"


@pytest.mark.parametrize("code,expected_count", [
    ("class Solution:\n    pass", 0),
    ("class Solution:\n    def a(self): return 1\n    def b(self): return 2", 2),
])
def test_python_refuses_to_guess_when_the_method_is_ambiguous(code, expected_count):
    """
    Zero or several public methods is an error the learner can act on, never a
    guess. Exit 2 keeps it distinguishable from a solution that merely crashed.
    """
    out, err, rc = run_python(code, "1")

    assert rc == 2
    assert "exactly one public method" in err
    assert f"found {expected_count}" in err


def test_python_runtime_errors_exit_non_zero():
    """
    The classification fix. This used to print to stdout and exit 0, so Judge0
    reported Accepted and the grader recorded wrong_answer for a crash.
    """
    code = "class Solution:\n    def solve(self, x):\n        raise ValueError('boom')"

    out, err, rc = run_python(code, "1")

    assert rc != 0
    assert "boom" in err
    assert "boom" not in out


def test_python_renders_booleans_in_the_canonical_lowercase():
    code = "class Solution:\n    def solve(self, x):\n        return True"

    out, _, _ = run_python(code, "1")

    assert out == "true"


def test_python_scalar_and_sequence_are_distinguished_by_annotation():
    """
    A single-token line is ambiguous — `5` could be the scalar 5 or the list
    [5]. The annotation resolves it; Java gets this free from static types.
    """
    annotated = "class Solution:\n    def solve(self, xs: list):\n        return len(xs)"
    scalar = "class Solution:\n    def solve(self, x: int):\n        return x + 1"

    assert run_python(annotated, "5")[0] == "1"
    assert run_python(scalar, "5")[0] == "6"


# ─────────────────────────────────────────────────────────────
# v2 JavaScript — executed for real
# ─────────────────────────────────────────────────────────────

def test_javascript_agrees_with_python_byte_for_byte():
    """
    The cross-language property the whole contract exists to establish. Under
    v1 Python emitted `[0,1]` and Java `0 1`, so one stored expected_output
    could not satisfy both.
    """
    py_out, _, _ = run_python(PY_TWO_SUM, "2 7 11 15\n9")
    js_out, err, rc = run_js(JS_TWO_SUM, "2 7 11 15\n9")

    assert rc == 0, err
    assert js_out == py_out == "0 1"


def test_javascript_refuses_to_guess_when_ambiguous():
    code = "class Solution { a() { return 1; } b() { return 2; } }"

    out, err, rc = run_js(code, "1")

    assert rc == 2
    assert "exactly one public method" in err


def test_javascript_private_helpers_are_ignored():
    code = textwrap.dedent("""
        class Solution {
            _helper(x) { return x * 2; }
            solve(n) { return this._helper(n); }
        }
    """)

    out, err, rc = run_js(code, "21")

    assert rc == 0, err
    assert out == "42"


def test_javascript_runtime_errors_exit_non_zero():
    code = "class Solution { solve(x) { throw new Error('boom'); } }"

    out, err, rc = run_js(code, "1")

    assert rc != 0
    assert "boom" in err
    assert "boom" not in out


# ─────────────────────────────────────────────────────────────
# Cross-language agreement on ambiguity
# ─────────────────────────────────────────────────────────────

AMBIGUITY_CASES = {
    "two public methods": (
        "class Solution:\n    def a(self, n): return 1\n    def b(self, n): return 2",
        "class Solution { a(n){return 1;} b(n){return 2;} }",
    ),
    "inherited public method": (
        "class Base:\n    def inherited(self): return 1\n"
        "class Solution(Base):\n    def solve(self, n): return n",
        "class Base { inherited(){return 1;} }\n"
        "class Solution extends Base { solve(n){return n;} }",
    ),
    "no public method": (
        "class Solution:\n    def _only(self, n): return n",
        "class Solution { _only(n){return n;} }",
    ),
}


@pytest.mark.parametrize("case", sorted(AMBIGUITY_CASES))
def test_python_and_javascript_agree_on_what_is_ambiguous(case):
    """
    A shared contract that two languages interpret differently is not a
    contract. Found in P2.6's own adversarial review: Python's `dir()` sees
    inherited methods and blocked, while JavaScript inspected only the own
    prototype and silently picked — the same submission accepted in one
    language and refused in the other.
    """
    python_code, js_code = AMBIGUITY_CASES[case]

    _, _, py_rc = run_python(python_code, "5")
    _, _, js_rc = run_js(js_code, "5")

    assert py_rc == 2, f"python accepted the ambiguous case {case!r}"
    assert js_rc == 2, f"javascript accepted the ambiguous case {case!r}"


def test_discovery_does_not_execute_learner_property_getters():
    """
    Reading a descriptor rather than the value: touching a getter during
    method discovery runs learner code before grading has begun, and a getter
    that raised would break discovery rather than the solution.
    """
    js_code = (
        "class Solution { get boom(){ throw new Error('executed'); } "
        "solve(n){ return n; } }"
    )

    out, err, rc = run_js(js_code, "7")

    assert rc == 0, err
    assert out == "7"
    assert "executed" not in err


# ─────────────────────────────────────────────────────────────
# The output contract — deliberately strict
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("expected,actual,should_match", [
    ("0 1", "0 1",       True),   # exact
    ("0 1", "0 1\n",     True),   # Judge0 always appends a newline
    ("0 1", "0 1   ",    True),   # trailing whitespace per line
    ("0 1", "   0 1",    True),   # leading whitespace
    ("0 1", "0 1\r\n",   True),   # CRLF
    ("",    "",          True),   # empty
    ("0 1", "0  1",      False),  # internal doubled space
    ("0 1", "0\n1",      False),  # newline is NOT a token separator
    ("0 1", "0\t1",      False),  # tab is NOT a token separator
    # Multi-line: the per-line rstrip is what handles trailing whitespace on
    # an INNER line. A whole-string strip() cannot reach it, so without these
    # rows removing the rstrip goes undetected — mutation testing found
    # exactly that.
    ("0 1\n2 3", "0 1   \n2 3",   True),
    ("0 1\n2 3", "0 1\n2 3   ",   True),
    ("0 1\n2 3", "0 1\r\n2 3",    True),
    ("0 1\n2 3", "0 1\n  2 3",    False),  # leading space on an inner line
])
def test_the_normalization_contract_is_exactly_this_and_no_broader(
    expected, actual, should_match
):
    """
    Pins what `normalize_output` accepts, because P2.6 must not silently
    broaden it. It trims each line and normalises line endings — nothing more.

    The three non-matching rows are the important ones: a solution printing
    `0\\n1` is NOT accepted for an expected `0 1`. The canonical contract is
    space-separated tokens ON ONE LINE, and loosening this later would let
    incorrect output pass, which is the failure direction that matters.
    """
    from groups.utils import normalize_output

    assert (normalize_output(expected) == normalize_output(actual)) is should_match


# ─────────────────────────────────────────────────────────────
# v1 — the UNIVERSAL fixes, which apply to existing questions
# ─────────────────────────────────────────────────────────────

def test_v1_python_runtime_errors_also_exit_non_zero():
    """
    The error-classification fix is deliberately NOT versioned: it cannot
    change a verdict (`all_passed` is False either way) and leaving v1
    misreporting would mean every existing question keeps recording crashes as
    wrong_answer — which is exactly the corrupted label P2.11 must not consume.

    Added after mutation testing: reverting the v1 fix left the entire suite
    green, because every other execution test drives v2.
    """
    from groups.services import GENERIC_PYTHON_WRAPPER

    source = GENERIC_PYTHON_WRAPPER.replace(
        "{user_code}",
        "class Solution:\n    def solve(self, x):\n        raise ValueError('boom')",
    )
    proc = subprocess.run([sys.executable, "-c", source], input="1",
                          capture_output=True, text=True, timeout=30)

    assert proc.returncode != 0, "a v1 crash still exits 0 and grades as wrong_answer"
    assert "boom" in proc.stderr
    assert "boom" not in proc.stdout


def test_v1_javascript_runtime_errors_also_exit_non_zero():
    from groups.services import GENERIC_JS_WRAPPER

    source = GENERIC_JS_WRAPPER.replace(
        "{user_code}", "class Solution { solve(x) { throw new Error('boom'); } }"
    )
    proc = subprocess.run(["node", "-e", source], input="1",
                          capture_output=True, text=True, timeout=30)

    assert proc.returncode != 0
    assert "boom" in proc.stderr
    assert "boom" not in proc.stdout


def test_v1_java_no_longer_swallows_exceptions():
    """No local JVM, so asserted structurally."""
    from groups.services import GENERIC_JAVA_WRAPPER

    assert "e.printStackTrace();" not in GENERIC_JAVA_WRAPPER
    assert "throw new RuntimeException(e)" in GENERIC_JAVA_WRAPPER


def test_v1_python_still_produces_its_original_output_shape():
    """
    The other half of the compatibility promise: the classification fix must
    not have altered what a SUCCESSFUL v1 run prints, or existing expected
    outputs would stop matching.
    """
    from groups.services import GENERIC_PYTHON_WRAPPER

    source = GENERIC_PYTHON_WRAPPER.replace(
        "{user_code}", "class Solution:\n    def solve(self, xs):\n        return [1, 2]"
    )
    proc = subprocess.run([sys.executable, "-c", source], input="[3]",
                          capture_output=True, text=True, timeout=30)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[1,2]", "v1 output shape changed"


# ─────────────────────────────────────────────────────────────
# Generated source must be syntactically valid
# ─────────────────────────────────────────────────────────────

def test_the_generated_python_source_actually_parses():
    """
    Regression for a real defect in this phase: the wrappers are applied with
    `.replace()`, not `.format()`, so the `{{`/`}}` escaping I first wrote
    stayed doubled in the generated source. Python's copy produced the literal
    text "found {}" and JavaScript's would not parse at all.
    """
    source = execution_contract.V2_PYTHON_WRAPPER.replace(
        "{user_code}", "class Solution:\n    def solve(self, x): return x"
    )

    compile(source, "<generated>", "exec")
    assert "{{" not in source and "}}" not in source


def test_the_generated_javascript_source_actually_parses():
    source = execution_contract.V2_JS_WRAPPER.replace(
        "{user_code}", "class Solution { solve(x) { return x; } }"
    )
    proc = subprocess.run(["node", "--check", "-"], input=source,
                          capture_output=True, text=True, timeout=30)

    assert proc.returncode == 0, proc.stderr
    assert "{{" not in source and "}}" not in source


def test_no_v2_wrapper_carries_doubled_braces():
    """
    Java has no local JVM here, so it cannot be parse-checked — but the defect
    is textual and this catches it in every wrapper including Java's.
    """
    for name, template in execution_contract.V2_WRAPPERS.items():
        assert "{{" not in template, f"{name} has doubled opening braces"
        assert "}}" not in template, f"{name} has doubled closing braces"
        assert template.count("{user_code}") == 1, f"{name} lost its placeholder"


# ─────────────────────────────────────────────────────────────
# v2 Java — structural (no local JVM)
# ─────────────────────────────────────────────────────────────

def test_java_v2_requires_exactly_one_public_method():
    java = execution_contract.V2_JAVA_WRAPPER

    assert "publicMethods.size() != 1" in java
    assert "exactly one public method" in java
    assert "System.exit(2)" in java


def test_java_v2_does_not_swallow_exceptions():
    """
    v1 called printStackTrace and returned normally, so the JVM exited 0 and a
    crash reached the grader as an empty stdout.
    """
    java = execution_contract.V2_JAVA_WRAPPER

    assert "e.printStackTrace()" not in java
    assert "throws Exception" in java


def test_java_v2_imports_cover_what_learners_actually_use():
    """
    `java.util.*` does NOT import subpackages, so a learner using Stream had
    their import regex-stripped and then failed to compile.
    """
    java = execution_contract.V2_JAVA_WRAPPER

    assert "import java.util.stream.*;" in java
    assert "import java.math.*;" in java


def test_java_v2_renders_space_separated():
    java = execution_contract.V2_JAVA_WRAPPER

    assert 'Collectors.joining(" ")' in java
    assert "StringJoiner" in java


# ─────────────────────────────────────────────────────────────
# Judge0 resource policy
# ─────────────────────────────────────────────────────────────

def _capture_judge0_payload(monkeypatch):
    from groups import coding_views

    captured = {}

    class FakeResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"status": {"id": 3, "description": "Accepted"},
                    "stdout": "", "stderr": "", "compile_output": "",
                    "time": "0.01", "memory": 100}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json or {})
        return FakeResponse()

    monkeypatch.setattr(coding_views.requests, "post", fake_post)
    coding_views._run_on_judge0("print(1)", "python", "1")
    return captured


def test_judge0_limits_are_omitted_unless_configured(monkeypatch):
    """
    The correction from P2.6's own adversarial review.

    Sending limits unconditionally looked free — they were "Judge0 CE's
    documented defaults" — but Judge0 enforces max_cpu_time_limit server-side
    and REJECTS anything above it, and our deployment's value is UNKNOWN from
    this repository (JUDGE0_API_HOST is `sync: false`). A rejected submission
    becomes GradingUnavailable, so the failure mode is a 503 on EVERY
    submission. Omitting the fields is exactly what production has always
    done.
    """
    monkeypatch.delenv("JUDGE0_CPU_TIME_LIMIT", raising=False)
    monkeypatch.delenv("JUDGE0_MEMORY_LIMIT", raising=False)

    captured = _capture_judge0_payload(monkeypatch)

    assert "cpu_time_limit" not in captured
    assert "memory_limit" not in captured


def test_judge0_limits_are_sent_when_an_operator_configures_them(monkeypatch):
    """The other half: opt-in must actually work once someone can verify the
    deployment's ceiling."""
    monkeypatch.setenv("JUDGE0_CPU_TIME_LIMIT", "2.5")
    monkeypatch.setenv("JUDGE0_MEMORY_LIMIT", "64000")

    captured = _capture_judge0_payload(monkeypatch)

    assert captured["cpu_time_limit"] == 2.5
    assert captured["memory_limit"] == 64000


def test_judge0_language_ids_come_from_the_one_registry():
    """
    No duplicate mapping. The registry is the authority; a second copy is how
    the js/javascript spelling drift produced three production bugs.
    """
    from common import languages
    from groups.coding_views import LANGUAGE_IDS

    assert LANGUAGE_IDS is languages.LANGUAGE_IDS


# ─────────────────────────────────────────────────────────────
# Grading classification
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status_id,expected", [
    (5, "time_limit"),
    (6, "compile_error"),
    (7, "runtime_error"),
    (11, "runtime_error"),
    (4, "wrong_answer"),
])
def test_execution_failures_are_not_collapsed_into_one_status(
    question, status_id, expected
):
    """
    compile_error, runtime_error, time_limit and wrong_answer are different
    evidence about a learner and must stay distinguishable — P2.11 depends on
    it, and a learner who cannot compile has not demonstrated a missing
    algorithm.
    """
    def runner(source, language, stdin):
        return {"status": "x", "status_id": status_id, "stdout": "nope",
                "stderr": "", "compile_output": "", "time": "0.01", "memory": 1}

    grade = GradingService(runner=runner).grade(question, "python", "print(1)")

    assert grade.final_status == expected
    assert grade.all_passed is False
