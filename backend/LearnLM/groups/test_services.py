"""
Milestone 3 seam tests for the extracted service layer.

The API-level suite (test_coding_views) drives everything through the
view, so it proves the orchestration — but it cannot see service-level
contracts that a careless refactor could silently change. The critical
one: GradeResult.stored_code must carry the Java import-stripped source,
because the strip happens before wrapping AND before persistence.
"""

import pytest

from groups.models import CodingPortal, Question, Topic
from groups.services import GradingService, GradingUnavailable


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


def test_runner_error_raises_grading_unavailable(question):
    def broken_runner(source_code, language, stdin):
        return {"error": "Judge0 timed out. Try again."}

    with pytest.raises(GradingUnavailable) as excinfo:
        GradingService(runner=broken_runner).grade(
            question, "python", "class Solution: pass"
        )

    assert excinfo.value.details == "Judge0 timed out. Try again."
