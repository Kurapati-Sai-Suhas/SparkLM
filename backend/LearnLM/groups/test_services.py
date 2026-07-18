"""
Milestone 3 seam tests for the extracted service layer.

The API-level suite (test_coding_views) drives everything through the
view, so it proves the orchestration — but it cannot see service-level
contracts that a careless refactor could silently change. The critical
one: GradeResult.stored_code must carry the Java import-stripped source,
because the strip happens before wrapping AND before persistence.
"""

import pytest

import subprocess
import sys

from groups.models import CodingPortal, Question, Topic
from groups.services import GENERIC_JS_WRAPPER, GENERIC_PYTHON_WRAPPER, GradingService, GradingUnavailable


def accepted_runner(stdout="1"):
    """Stub runner returning an accepted Judge0 verdict for every case."""
    def run(source_code, language, stdin):
        return {
            "status": "Accepted",
            "status_id": 3,
            "stdout": stdout,
            "stderr": "",
            "compile_output": "",
            "time": "0.01",
            "memory": 1000,
        }
    return run


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="Seam Test Portal")
    topic, _ = Topic.objects.get_or_create(
        name="Array", defaults={"structure_type": "flat", "portal": portal}
    )
    return Question.objects.create(
        title="Echo Problem",
        content="Return the input.",
        topic=topic,
        base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={},
    )


def test_java_stored_code_is_import_stripped(question):
    # Java imports are stripped before wrapping (they break compilation
    # inside the harness) and the stripped source is what gets persisted —
    # stored_code must reflect that, or submissions would start storing
    # code that never actually compiled.
    raw = "import java.util.*;\nclass Solution { public int f(int x) { return x; } }"
    grade = GradingService(runner=accepted_runner()).grade(question, "java", raw)

    assert "import java.util." not in grade.stored_code
    assert "class Solution" in grade.stored_code
    assert grade.all_passed


def test_python_stored_code_is_unchanged(question):
    raw = "class Solution:\n    def f(self, x):\n        return x"
    grade = GradingService(runner=accepted_runner()).grade(question, "python", raw)

    assert grade.stored_code == raw
    assert grade.final_status == "accepted"


def test_python_wrapper_compiles_without_syntax_error():
    generated = GENERIC_PYTHON_WRAPPER.replace(
        "{user_code}", "class Solution:\n    def f(self, x):\n        return x\n"
    )
    compile(generated, "<generated>", "exec")


def test_python_wrapper_parses_multiline_json_stdin_as_separate_args():
    # Real case found in the seeded bank: two-parameter questions store
    # one JSON array per line (e.g. nums1, nums2) — invalid as a single
    # JSON document. Before the fix this fell through to treating the
    # whole multi-line string as ONE opaque argument, so any correct
    # solution to such a question failed grading unconditionally.
    user_code = (
        "class Solution:\n"
        "    def combine(self, a, b):\n"
        "        return a + b\n"
    )
    generated = GENERIC_PYTHON_WRAPPER.replace("{user_code}", user_code)
    result = subprocess.run(
        [sys.executable, "-c", generated],
        input="[1,3]\n[2]", capture_output=True, text=True, timeout=10,
    )
    assert result.stdout.strip() == "[1,3,2]"


def test_python_wrapper_single_value_regression():
    user_code = "class Solution:\n    def solve(self, x):\n        return x\n"
    generated = GENERIC_PYTHON_WRAPPER.replace("{user_code}", user_code)
    result = subprocess.run(
        [sys.executable, "-c", generated],
        input="42", capture_output=True, text=True, timeout=10,
    )
    assert result.stdout.strip() == "42"


def test_js_wrapper_parses_multiline_json_stdin_as_separate_args():
    user_code = (
        "class Solution {\n"
        "    combine(a, b) { return [...a, ...b]; }\n"
        "}\n"
    )
    generated = GENERIC_JS_WRAPPER.replace("{user_code}", user_code)
    result = subprocess.run(
        ["node", "-e", generated],
        input="[1,3]\n[2]", capture_output=True, text=True, timeout=10,
    )
    assert result.stdout.strip() == "[1,3,2]"


def test_js_wrapper_single_line_array_spread_regression():
    user_code = "class Solution {\n    sum3(a, b, c) { return a + b + c; }\n}\n"
    generated = GENERIC_JS_WRAPPER.replace("{user_code}", user_code)
    result = subprocess.run(
        ["node", "-e", generated],
        input="[1,2,3]", capture_output=True, text=True, timeout=10,
    )
    assert result.stdout.strip() == "6"


def test_runner_error_raises_grading_unavailable(question):
    def broken_runner(source_code, language, stdin):
        return {"error": "Judge0 timed out. Try again."}

    with pytest.raises(GradingUnavailable) as excinfo:
        GradingService(runner=broken_runner).grade(
            question, "python", "class Solution: pass"
        )

    assert excinfo.value.details == "Judge0 timed out. Try again."
