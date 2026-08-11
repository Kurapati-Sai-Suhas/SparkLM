"""
Hidden grading data must never leave the server (M2 P2.5, Phase 2).

`CodeSubmitView` returned `test_results` — the full per-case grading detail —
and each entry carried two fields that together handed a learner the entire
hidden suite:

    "expected_output": expected   the answer key for that hidden case
    "your_output":     actual     their program's output on that hidden input

The second is the worse of the pair and the less obvious one. It is not merely
"their own output": submitting

    print(input())

echoes the hidden INPUT back through `your_output`. Two ordinary submissions —
one echoing stdin, one doing nothing — reconstruct every hidden input and every
expected output of a problem, from documented API responses, with no exploit.

`_sample_case` already stated the rule this violated, two hundred lines above
the leak: "Grading data never leaves the server."

The detail still exists server-side. `ProgressionService` reads timings out of
it and stores the whole structure on `AgenticCoachLog.error_logs`. It simply
stops being serialised to the client.

These tests assert on the SERIALISED response body, not on the view's return
value, because that is what an attacker actually receives.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from groups import coding_views
from groups.models import CodingPortal, Question, Topic

User = get_user_model()

# Values planted in the hidden suite. If any of these strings appears anywhere
# in a response body, grading data escaped.
SECRET_INPUT = "SECRETHIDDENINPUT9137"
SECRET_EXPECTED = "SECRETANSWERKEY4471"


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="secrecy", password="Secrecy#2026x", email="sec@t.com"
    )


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="Secrecy Portal")
    topic, _ = Topic.objects.get_or_create(
        name="SecrecyTopic", defaults={"structure_type": "flat", "portal": portal}
    )
    return Question.objects.create(
        title="Secrecy Problem",
        content="Return the input.",
        topic=topic,
        base_difficulty=1200.0,
        hidden_test_cases=[
            {"stdin": SECRET_INPUT, "expected_output": SECRET_EXPECTED},
            {"stdin": f"{SECRET_INPUT}-2", "expected_output": f"{SECRET_EXPECTED}-2"},
        ],
        hidden_wrapper_code={},
    )


def judge0_returning(stdout):
    def runner(source_code, language, stdin):
        return {
            "status": "Accepted", "status_id": 3, "stdout": stdout,
            "stderr": "", "compile_output": "", "time": "0.01", "memory": 1000,
        }
    return runner


def submit(client, question, monkeypatch, stdout=""):
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_returning(stdout))
    return client.post(
        reverse("code-submit"),
        {"problem_id": question.id, "language": "python", "code": "print(1)"},
        format="json",
    )


def body_of(response):
    """The bytes the client actually receives."""
    response.render() if hasattr(response, "render") and not response.is_rendered else None
    return response.content.decode()


# ─────────────────────────────────────────────────────────────
# The leak itself
# ─────────────────────────────────────────────────────────────

def test_the_answer_key_never_appears_in_the_submit_response(
    user, question, monkeypatch
):
    client = APIClient()
    client.force_authenticate(user=user)

    response = submit(client, question, monkeypatch, stdout="wrong")
    raw = body_of(response)

    assert response.status_code == 200
    assert SECRET_EXPECTED not in raw, "the hidden expected output was returned"
    assert "expected_output" not in raw, "the expected_output key was returned"


def test_echoing_stdin_cannot_exfiltrate_the_hidden_input(
    user, question, monkeypatch
):
    """
    The `your_output` half of the leak, driven the way an attacker would: the
    submitted program echoes stdin, so a response carrying its output would
    carry the hidden input verbatim.
    """
    client = APIClient()
    client.force_authenticate(user=user)

    response = submit(client, question, monkeypatch, stdout=SECRET_INPUT)
    raw = body_of(response)

    assert SECRET_INPUT not in raw, "the hidden input was echoed back to the client"
    assert "your_output" not in raw


def test_the_submit_response_carries_no_per_case_structure_at_all(
    user, question, monkeypatch
):
    """
    Belt and braces on the shape rather than on the values: no `test_results`
    key, and no list of per-case dicts under any other name.
    """
    client = APIClient()
    client.force_authenticate(user=user)

    response = submit(client, question, monkeypatch, stdout="wrong")
    payload = json.loads(body_of(response))

    assert "test_results" not in payload
    for key, value in payload.items():
        assert not (isinstance(value, list) and value and isinstance(value[0], dict)), (
            f"'{key}' carries a per-case structure: {value[:1]}"
        )


def test_the_response_exposes_only_the_agreed_safe_fields(
    user, question, monkeypatch
):
    """
    Allowlist, not denylist. A denylist passes the moment grading data is
    added under a name nobody thought to forbid.
    """
    client = APIClient()
    client.force_authenticate(user=user)

    response = submit(client, question, monkeypatch, stdout="wrong")
    payload = json.loads(body_of(response))

    assert set(payload) <= {
        "submission_id", "status", "message", "passed", "total", "all_passed",
        "runtime_ms", "memory_kb", "elo_update", "success_rate", "agentic_hint",
    }, f"unexpected field(s) in the submit response: {sorted(payload)}"


def test_the_verdict_message_does_not_name_the_failing_case(
    user, question, monkeypatch
):
    """
    Naming the failing case is itself a leak: repeated submissions would let a
    learner bisect the hidden suite even with the answer key removed.
    """
    client = APIClient()
    client.force_authenticate(user=user)

    response = submit(client, question, monkeypatch, stdout="wrong")
    payload = json.loads(body_of(response))

    assert payload["status"] == "wrong_answer"
    assert payload["message"] == (
        "Wrong Answer: your solution failed one or more hidden tests."
    )
    assert "test_case" not in body_of(response)


def test_counts_are_still_reported_so_progress_stays_visible(
    user, question, monkeypatch
):
    """
    The fix must not blind the learner. passed/total drive the portal's
    progress bar, and removing them would trade a leak for a worse product.
    """
    client = APIClient()
    client.force_authenticate(user=user)

    response = submit(client, question, monkeypatch, stdout=SECRET_EXPECTED)
    payload = json.loads(body_of(response))

    assert payload["total"] == 2
    assert payload["passed"] == 1, "only the first case's expected output matches"
    assert payload["all_passed"] is False
    assert "runtime_ms" in payload and "memory_kb" in payload


# ─────────────────────────────────────────────────────────────
# The detail must survive server-side
# ─────────────────────────────────────────────────────────────

def test_the_grading_detail_is_still_available_to_the_server(
    user, question, monkeypatch
):
    """
    The detail was not deleted, only unpublished — timings are read from it
    and it is stored for the coach. A "fix" that stopped producing it would
    silently break execution_time_ms and the coach's error logs.
    """
    from groups.services import GradingService

    grade = GradingService(runner=judge0_returning(SECRET_EXPECTED)).grade(
        question, "python", "print(1)"
    )

    assert len(grade.results) == 2
    assert grade.results[0]["expected_output"] == SECRET_EXPECTED
    assert "your_output" in grade.results[0]


def test_submission_timings_are_still_persisted(user, question, monkeypatch):
    from groups.models import CodeSubmission

    client = APIClient()
    client.force_authenticate(user=user)

    submit(client, question, monkeypatch, stdout=SECRET_EXPECTED)

    submission = CodeSubmission.objects.get(user=user)
    assert submission.execution_time_ms is not None
    assert submission.memory_used_kb is not None


# ─────────────────────────────────────────────────────────────
# The other learner-facing surfaces
# ─────────────────────────────────────────────────────────────

def test_the_problem_endpoint_ships_sample_input_but_never_the_answer(
    user, question, monkeypatch
):
    """
    `_sample_case` is allowed to ship case 1's stdin — it is already public in
    the problem's Examples block — but never its expected output, which for a
    single-case problem IS the answer key.
    """
    from groups.hybrid_router import RoutingClassifier
    monkeypatch.setattr(RoutingClassifier, "predict_route", lambda self, *a, **k: "flat")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get(reverse("code-next-problem"), {"topic": "SecrecyTopic"})
    raw = body_of(response)

    assert response.status_code == 200
    assert SECRET_EXPECTED not in raw, "the problem endpoint leaked the answer key"
    assert "expected_output" not in raw


def test_run_never_reaches_hidden_grading_data(user, question, monkeypatch):
    """
    Run executes client-supplied stdin only. It must not consult the hidden
    suite, so it cannot leak from it either.
    """
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_returning("anything"))
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        reverse("code-run"),
        {"problem_id": question.id, "language": "python",
         "code": "print(1)", "stdin": "5"},
        format="json",
    )
    raw = body_of(response)

    assert SECRET_EXPECTED not in raw
    assert SECRET_INPUT not in raw


# ─────────────────────────────────────────────────────────────
# Phase 3 — a caseless problem is a controlled error, never invention
# ─────────────────────────────────────────────────────────────

def test_submitting_to_a_caseless_problem_is_a_controlled_409(user, question):
    """
    Not a 500, and not a silent pass. `question_not_gradable` is the existing
    convention and it stays.
    """
    question.hidden_test_cases = []
    question.save(update_fields=["hidden_test_cases"])
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        reverse("code-submit"),
        {"problem_id": question.id, "language": "python", "code": "print(1)"},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["detail"] == "question_not_gradable"


def test_a_caseless_problem_is_never_armed_by_submitting_to_it(user, question):
    """
    The submission path must not write grading data either — the same
    invariant Phase 3 enforces on the problem-serving path.
    """
    question.hidden_test_cases = []
    question.save(update_fields=["hidden_test_cases"])
    client = APIClient()
    client.force_authenticate(user=user)

    client.post(
        reverse("code-submit"),
        {"problem_id": question.id, "language": "python", "code": "print(1)"},
        format="json",
    )

    question.refresh_from_db()
    assert question.hidden_test_cases == []
