"""
The adaptive coding loop, end to end (M2 P2.7 follow-up, Step 5).

    next problem -> submit -> grade -> ProgressionService -> learner state
                 -> next problem again

The individual rules are already covered — `test_routing_hygiene` owns the
P2.8a ordering, `test_learning_signal_hygiene` owns the evidence rules,
`test_shadow_adaptive` owns Glicko. What none of them do is walk the WHOLE
circuit in one test, which is the only way to catch a break in the joins
between them rather than inside any one of them.

Judge0 is mocked at `coding_views._run_on_judge0`, the same seam the rest of
the suite uses. No network, no production, local test database only.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

import groups.coding_views as coding_views
from groups.models import (
    CodeSubmission, CodingPortal, Question, Topic, UserCodingProfile,
    UserTopicMastery,
)

User = get_user_model()

SOLUTION = "class Solution:\n    def solve(self, input_val):\n        return input_val\n"


def judge0_mock(stdout="1", status_id=3, status="Accepted"):
    def _mock(source_code, language, stdin=""):
        return {"status": status, "status_id": status_id, "stdout": stdout,
                "stderr": "", "compile_output": "", "time": "0.05",
                "memory": 3000}
    return _mock


@pytest.fixture
def learner(db):
    user = User.objects.create_user(
        username="looper", password="password", email="looper@example.com")
    UserCodingProfile.objects.create(user=user, elo_rating=1200)
    return user


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Loop Portal")
    made, _ = Topic.objects.get_or_create(
        name="LoopTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


@pytest.fixture
def questions(db, topic):
    """
    Three questions at the learner's rating, so selection is decided by the
    tie-breakers rather than by difficulty — which is what makes the exclusion
    and cooldown rules observable in the loop.
    """
    return [
        Question.objects.create(
            id=8100 + n, title=f"Loop {n}", content="Return the input.",
            topic=topic, base_difficulty=1200.0,
            hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
            hidden_wrapper_code={},
            status=Question.STATUS_PUBLISHED,
            trust_state=Question.TRUST_ORACLE_VERIFIED,
        )
        for n in range(3)
    ]


@pytest.fixture
def client(learner):
    api = APIClient()
    api.force_authenticate(user=learner)
    return api


def next_problem(client, topic_name="LoopTopic"):
    return client.get(reverse("code-next-problem"), {"topic": topic_name})


def submit(client, question, code=SOLUTION, language="python"):
    return client.post(reverse("code-submit"), {
        "problem_id": question.id, "code": code, "language": language,
    }, format="json")


@pytest.mark.django_db
def test_the_whole_loop_runs_and_advances_the_learner(
        client, learner, questions, monkeypatch):
    """One full circuit: served, solved, recorded, and not served again."""
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="1"))

    served = next_problem(client)
    assert served.status_code == 200
    first_id = int(served.data["id"])

    graded = submit(client, Question.objects.get(id=first_id))
    assert graded.status_code == 200
    assert graded.data["status"] == "accepted"

    # ProgressionService ran: submission stored, mastery moved, Elo moved.
    assert CodeSubmission.objects.filter(user=learner, question_id=first_id).exists()
    mastery = UserTopicMastery.objects.get(user=learner, topic__name="LoopTopic")
    assert mastery.accuracy == 1.0
    assert mastery.reviews == 1
    assert graded.data["elo_update"]["rating_change"] > 0

    # The loop closes: a solved question is gone from the route for good.
    again = next_problem(client)
    assert again.status_code == 200
    assert int(again.data["id"]) != first_id


@pytest.mark.django_db
def test_solving_everything_exhausts_the_topic_rather_than_repeating(
        client, questions, monkeypatch):
    """
    The exclusion is permanent, so the loop must terminate in a typed
    "exhausted" response rather than by serving something twice.
    """
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="1"))

    seen = []
    for _ in range(len(questions)):
        served = next_problem(client)
        assert served.status_code == 200, served.data
        question_id = int(served.data["id"])
        assert question_id not in seen, "a solved question was served again"
        seen.append(question_id)
        assert submit(client, Question.objects.get(id=question_id)).status_code == 200

    exhausted = next_problem(client)
    assert exhausted.data.get("next_problem") is None
    assert exhausted.data["status"] in ("topic_exhausted", "completed")
    assert len(seen) == len(questions)


@pytest.mark.django_db
def test_a_wrong_answer_keeps_the_question_but_demotes_it(
        client, learner, questions, monkeypatch):
    """
    Failing is not solving: the question stays in the pool, but the cooldown
    pushes it behind untried ones. Both halves matter — dropping it would lose
    the retry, and serving it immediately would loop the learner on a failure.
    """
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="wrong"))

    served = next_problem(client)
    failed_id = int(served.data["id"])
    graded = submit(client, Question.objects.get(id=failed_id))
    assert graded.data["status"] == "wrong_answer"

    following = next_problem(client)
    assert int(following.data["id"]) != failed_id

    mastery = UserTopicMastery.objects.get(user=learner, topic__name="LoopTopic")
    assert mastery.accuracy == 0.0
    assert mastery.reviews == 1


@pytest.mark.django_db
def test_an_execution_failure_is_practice_but_not_evidence(
        client, learner, questions, monkeypatch):
    """
    P2.8b's rule, observed through the real loop rather than the service: a
    compile error says nothing about whether the learner knows the topic, so it
    must not enter the accuracy statistic.
    """
    monkeypatch.setattr(
        coding_views, "_run_on_judge0",
        judge0_mock(stdout="", status_id=6, status="Compilation Error"))

    served = next_problem(client)
    graded = submit(client, Question.objects.get(id=int(served.data["id"])))
    assert graded.data["status"] == "compile_error"

    rows = UserTopicMastery.objects.filter(user=learner, topic__name="LoopTopic")
    if rows.exists():
        assert rows.get().reviews == 0, "a compile error became evidence"


@pytest.mark.django_db
def test_serving_a_question_never_writes_learner_state(client, questions):
    """Reading the route must not itself teach the learner model."""
    before = list(UserTopicMastery.objects.values_list("id", flat=True))
    next_problem(client)
    next_problem(client)
    assert list(UserTopicMastery.objects.values_list("id", flat=True)) == before
    assert not CodeSubmission.objects.exists()


@pytest.mark.django_db
def test_a_question_with_unexecutable_test_data_does_not_break_the_loop(
        client, questions, monkeypatch):
    """
    The join between this phase's 409 and the loop. A question whose stored
    test data cannot be executed must refuse cleanly and leave the learner able
    to carry on, not poison the circuit.
    """
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="1"))

    broken = questions[0]
    broken.hidden_test_cases = [{"stdin": "1", "expected_output": ["1"]}]
    broken.save(update_fields=["hidden_test_cases"])

    client.raise_request_exception = False
    refused = submit(client, broken)
    assert refused.status_code == 409
    assert refused.data["detail"] == "question_not_gradable"
    assert not CodeSubmission.objects.filter(question=broken).exists()

    # The loop still works afterwards.
    healthy = questions[1]
    assert submit(client, healthy).status_code == 200
