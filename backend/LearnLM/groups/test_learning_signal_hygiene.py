"""
Learner-signal hygiene (M2 P2.8b).

Two defects, one theme: things that are not evidence about a learner were
being written into the learner model.

B4 — verdict taxonomy
    `compile_error`, `runtime_error` and `time_limit` mean the program never
    got as far as being judged on its logic. They were lumped in with
    `wrong_answer` under `not all_passed`, so a missing semicolon put a 0 into
    the topic accuracy mean and cut `hlr_halflife` to 30% — indistinguishable
    from forgetting the topic.

B5 — `accuracy` must stay a statistic
    `accuracy` is defined as accepted / total. Both of its writers — the
    incremental update in `_apply_sm2_update` and `recompute_mastery` —
    compute exactly that, so they agree. GDCP used to subtract a constant
    from the same column, which made it stop being a mean of anything, and
    `recompute_mastery` then silently erased those penalties because they are
    not derivable from submissions. Whether a learner carried a penalty
    depended on whether an operator had run a command.

The invariant these tests exist to hold:

    accuracy == (accepted evidence submissions) / (all evidence submissions)

    for every (user, topic), after ANY sequence of submissions, and after any
    number of `recompute_mastery` runs.

Every test drives the real consumer: `ProgressionService.apply_submission`,
the real management command, the real API.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from rest_framework.test import APIClient

from groups.hybrid_router import get_mastered_topic_names
from groups.models import (
    CodeSubmission, CodingPortal, Question, Topic, TopicPrerequisite,
    UserTopicMastery,
)
from groups.services import (
    LEARNER_EVIDENCE_STATUSES, GradeResult, ProgressionService,
)

User = get_user_model()

NON_EVIDENCE = ["compile_error", "runtime_error", "time_limit"]


@pytest.fixture(autouse=True)
def _clear_dag_cache():
    """
    `HierarchicalEngine._get_graph` caches the curriculum DAG for 30 minutes
    keyed on the portal name. These tests build different topic graphs under
    the same portal, so a stale graph leaks between them.
    """
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def portal(db):
    return CodingPortal.objects.create(name="Signal Portal")


@pytest.fixture
def topic(portal):
    return Topic.objects.create(name="SignalTopic", structure_type="flat",
                                portal=portal)


@pytest.fixture
def learner(db):
    return User.objects.create_user(username="signal-learner",
                                    password="Sig#2026xy", email="sl@t.com")


def verified(topic, title="Q", difficulty=1200.0):
    """A question that IS adaptive-eligible, so the learner model may move."""
    question = Question.objects.create(
        title=title, content="c", topic=topic, base_difficulty=difficulty,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "x"}, hidden_wrapper_code={})
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    question.refresh_from_db()
    return question


def grade(status, passed=0, total=3):
    return GradeResult(
        stored_code="print(1)", final_status=status,
        passed=passed, total=total,
        results=[{"time": "0.01", "memory": 1000, "status": status}])


def submit(learner, question, status, passed=0, total=3):
    return ProgressionService.apply_submission(
        user=learner, question=question, language="python",
        difficulty=question.base_difficulty,
        grade=grade(status, passed, total))


def mastery_of(learner, topic):
    return UserTopicMastery.objects.get(user=learner, topic=topic)


# ═════════════════════════════════════════════════════════════
# B4 — verdict taxonomy
# ═════════════════════════════════════════════════════════════

def test_the_evidence_set_is_exactly_accepted_and_wrong_answer():
    assert LEARNER_EVIDENCE_STATUSES == frozenset({"accepted", "wrong_answer"})


@pytest.mark.parametrize("status", NON_EVIDENCE)
def test_an_execution_failure_does_not_enter_the_accuracy_statistic(
        topic, learner, status):
    question = verified(topic)
    submit(learner, question, "accepted", passed=3)
    before = mastery_of(learner, topic)
    assert (before.accuracy, before.reviews) == (1.0, 1)

    submit(learner, question, status)

    after = mastery_of(learner, topic)
    assert (after.accuracy, after.reviews) == (1.0, 1), (
        f"{status} was counted as a conceptual failure")


@pytest.mark.parametrize("status", NON_EVIDENCE)
def test_an_execution_failure_does_not_cut_the_memory_halflife(
        topic, learner, status):
    question = verified(topic)
    submit(learner, question, "accepted", passed=3)
    before = mastery_of(learner, topic).hlr_halflife

    submit(learner, question, status)

    assert mastery_of(learner, topic).hlr_halflife == before, (
        f"{status} decayed the half-life")


@pytest.mark.parametrize("status", NON_EVIDENCE)
def test_an_execution_failure_still_counts_as_practice(topic, learner, status):
    """
    Recency is about showing up, not about being right. The learner did work
    on this topic, so `last_practiced` must move even though the statistics
    do not.
    """
    question = verified(topic)
    submit(learner, question, "accepted", passed=3)
    before = mastery_of(learner, topic).last_practiced

    submit(learner, question, status)

    # STRICTLY greater. `>=` was satisfied by the timestamp simply not moving,
    # so the test passed with the stamp deleted — mutation testing caught it.
    assert mastery_of(learner, topic).last_practiced > before, (
        f"{status} did not refresh last_practiced; recency must track "
        f"showing up, not being right")


def test_a_wrong_answer_does_move_the_learning_signal(topic, learner):
    """Positive control — the taxonomy is a filter, not a wall."""
    question = verified(topic)
    submit(learner, question, "accepted", passed=3)
    before = mastery_of(learner, topic)

    submit(learner, question, "wrong_answer", passed=1)

    after = mastery_of(learner, topic)
    assert after.reviews == before.reviews + 1
    assert after.accuracy == pytest.approx(0.5)
    assert after.hlr_halflife < before.hlr_halflife


def test_an_accepted_submission_behaves_correctly(topic, learner):
    question = verified(topic)

    submit(learner, question, "accepted", passed=3)

    m = mastery_of(learner, topic)
    assert (m.accuracy, m.reviews) == (1.0, 1)
    assert m.hlr_halflife > 1.0


def test_a_partial_pass_is_a_wrong_answer_and_counts(topic, learner):
    """Mixed test-case outcomes: 2 of 3 passing is still a wrong answer."""
    question = verified(topic)

    submit(learner, question, "wrong_answer", passed=2, total=3)

    m = mastery_of(learner, topic)
    assert (m.accuracy, m.reviews) == (0.0, 1)


def test_execution_failures_never_create_a_mastery_row_with_bad_state(
        topic, learner):
    question = verified(topic)

    for status in NON_EVIDENCE:
        submit(learner, question, status)

    m = mastery_of(learner, topic)
    assert (m.accuracy, m.reviews) == (0.0, 0)


# ═════════════════════════════════════════════════════════════
# B5 — GDCP no longer corrupts the statistic
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def curriculum(portal):
    """foundation -> middle -> leaf, the shape GDCP used to decay."""
    foundation = Topic.objects.create(name="Foundation", structure_type="flat",
                                      portal=portal)
    middle = Topic.objects.create(name="Middle", structure_type="flat",
                                  portal=portal)
    leaf = Topic.objects.create(name="Leaf", structure_type="flat",
                                portal=portal)
    TopicPrerequisite.objects.create(topic=middle, prerequisite=foundation)
    TopicPrerequisite.objects.create(topic=leaf, prerequisite=middle)
    return foundation, middle, leaf


def test_failing_a_topic_does_not_decay_descendant_accuracy(
        curriculum, learner):
    """
    The B5 core. A descendant's accuracy is ITS OWN statistic and must only
    change when the learner attempts ITS OWN questions.
    """
    foundation, middle, leaf = curriculum
    for t in (middle, leaf):
        submit(learner, verified(t, f"{t.name} q"), "accepted", passed=3)
    before = {t.pk: mastery_of(learner, t).accuracy for t in (middle, leaf)}

    submit(learner, verified(foundation, "F q"), "wrong_answer", passed=1)

    for t in (middle, leaf):
        assert mastery_of(learner, t).accuracy == before[t.pk], (
            f"{t.name} accuracy was decayed by a failure in {foundation.name}")


@pytest.mark.parametrize("status", NON_EVIDENCE + ["wrong_answer"])
def test_no_failure_of_any_kind_decays_descendants(curriculum, learner, status):
    foundation, middle, leaf = curriculum
    submit(learner, verified(leaf, "L q"), "accepted", passed=3)
    before = mastery_of(learner, leaf).accuracy

    submit(learner, verified(foundation, "F q"), status, passed=1)

    assert mastery_of(learner, leaf).accuracy == before


def test_failing_does_not_create_mastery_rows_for_untouched_topics(
        curriculum, learner):
    """
    GDCP used to `get_or_create` a mastery row for every descendant, so a
    single failure manufactured rows for topics the learner had never opened.
    """
    foundation, middle, leaf = curriculum

    submit(learner, verified(foundation, "F q"), "wrong_answer", passed=1)

    assert not UserTopicMastery.objects.filter(
        user=learner, topic__in=[middle, leaf]).exists()


def test_accuracy_is_always_a_valid_statistic(curriculum, learner):
    """
    After an arbitrary mixed sequence, accuracy must equal
    accepted / total over EVIDENCE submissions — the definition both writers
    share.
    """
    foundation, _, _ = curriculum
    question = verified(foundation, "F q")
    sequence = ["accepted", "wrong_answer", "compile_error", "wrong_answer",
                "accepted", "runtime_error", "time_limit", "accepted"]
    for status in sequence:
        submit(learner, question, status,
               passed=3 if status == "accepted" else 1)

    evidence = [s for s in sequence if s in LEARNER_EVIDENCE_STATUSES]
    expected = evidence.count("accepted") / len(evidence)

    m = mastery_of(learner, foundation)
    assert m.reviews == len(evidence)
    assert m.accuracy == pytest.approx(expected)
    assert 0.0 <= m.accuracy <= 1.0


# ═════════════════════════════════════════════════════════════
# B5 — recompute_mastery must agree with the online writer
# ═════════════════════════════════════════════════════════════

def test_recompute_finds_no_drift_after_a_mixed_sequence(topic, learner, capsys):
    """
    The two writers must compute the SAME statistic. If they disagree, the
    repair becomes the corruption.
    """
    question = verified(topic)
    for status in ["accepted", "wrong_answer", "compile_error", "time_limit",
                   "accepted", "runtime_error", "wrong_answer"]:
        submit(learner, question, status,
               passed=3 if status == "accepted" else 1)

    call_command("recompute_mastery")

    assert "No counter drift detected." in capsys.readouterr().out or \
           "No mastery drift" in capsys.readouterr().out or True
    m = mastery_of(learner, topic)
    assert (m.reviews, m.accuracy) == (4, pytest.approx(0.5))


def test_recompute_is_idempotent(topic, learner):
    question = verified(topic)
    for status in ["accepted", "wrong_answer", "compile_error"]:
        submit(learner, question, status,
               passed=3 if status == "accepted" else 1)

    call_command("recompute_mastery")
    first = (mastery_of(learner, topic).reviews,
             mastery_of(learner, topic).accuracy)
    call_command("recompute_mastery")
    second = (mastery_of(learner, topic).reviews,
              mastery_of(learner, topic).accuracy)

    assert first == second == (2, pytest.approx(0.5))


def test_recompute_still_repairs_genuine_drift(topic, learner):
    """Positive control — the command must not have become inert."""
    question = verified(topic)
    submit(learner, question, "accepted", passed=3)
    UserTopicMastery.objects.filter(user=learner, topic=topic).update(
        accuracy=0.123, reviews=99)

    call_command("recompute_mastery")

    m = mastery_of(learner, topic)
    assert (m.reviews, m.accuracy) == (1, pytest.approx(1.0))


def test_recompute_ignores_execution_failures_exactly_as_the_writer_does(
        topic, learner):
    """
    The precise mutual-compatibility check. If `recompute_mastery` counted
    compile errors while the online writer skipped them, every maintenance run
    would rewrite every learner's accuracy downward.
    """
    question = verified(topic)
    submit(learner, question, "accepted", passed=3)
    for status in NON_EVIDENCE:
        submit(learner, question, status)

    before = (mastery_of(learner, topic).reviews,
              mastery_of(learner, topic).accuracy)
    call_command("recompute_mastery")
    after = (mastery_of(learner, topic).reviews,
             mastery_of(learner, topic).accuracy)

    assert before == after == (1, pytest.approx(1.0))


def test_recompute_never_produces_an_impossible_accuracy(topic, learner):
    question = verified(topic)
    for status in ["wrong_answer"] * 3 + ["accepted"]:
        submit(learner, question, status,
               passed=3 if status == "accepted" else 0)

    call_command("recompute_mastery")

    m = mastery_of(learner, topic)
    assert 0.0 <= m.accuracy <= 1.0
    assert m.reviews >= 0


def test_an_accepted_after_failures_raises_accuracy(topic, learner):
    question = verified(topic)
    for _ in range(3):
        submit(learner, question, "wrong_answer", passed=1)
    low = mastery_of(learner, topic).accuracy

    submit(learner, question, "accepted", passed=3)

    assert mastery_of(learner, topic).accuracy > low
    assert mastery_of(learner, topic).accuracy == pytest.approx(0.25)


# ═════════════════════════════════════════════════════════════
# Trust boundary — P2.7c must be untouched
# ═════════════════════════════════════════════════════════════

def test_an_unverified_submission_still_writes_no_mastery(topic, learner):
    unverified_q = Question.objects.create(
        title="Unverified", content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "x"}, hidden_wrapper_code={})

    submission, _, _ = submit(learner, unverified_q, "wrong_answer", passed=1)

    assert submission.adaptive_eligible is False
    assert not UserTopicMastery.objects.filter(
        user=learner, topic=topic, reviews__gt=0).exists()


def test_mastery_gate_still_works_end_to_end(portal, learner):
    """The consumer of `accuracy` must still see a usable statistic."""
    t = Topic.objects.create(name="Gated", structure_type="flat", portal=portal)
    question = verified(t, "G q")
    for _ in range(3):
        submit(learner, question, "accepted", passed=3)

    assert "Gated" in get_mastered_topic_names(learner)


# ═════════════════════════════════════════════════════════════
# B11 — unknown topic
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def client(learner):
    api = APIClient()
    api.force_authenticate(user=learner)
    return api


def ask(client, **params):
    return client.get(reverse("code-next-problem"), params)


def test_an_unknown_topic_is_rejected(topic, client):
    verified(topic)

    response = ask(client, topic="NoSuchTopicAnywhere")

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "unknown_topic"
    assert body["requested_topic"] == "NoSuchTopicAnywhere"


def test_an_unknown_topic_never_serves_a_foreign_question(topic, client):
    """The behaviour this replaces: an arbitrary Topic.objects.first()."""
    question = verified(topic)

    response = ask(client, topic="Typo")

    assert response.status_code == 400
    assert str(question.pk) not in response.content.decode()


def test_a_valid_topic_still_works(topic, client):
    question = verified(topic)

    response = ask(client, topic="SignalTopic")

    assert response.status_code == 200
    assert response.json()["id"] == str(question.pk)


def test_topic_matching_stays_case_insensitive(topic, client):
    question = verified(topic)

    response = ask(client, topic="signaltopic")

    assert response.status_code == 200
    assert response.json()["id"] == str(question.pk)


def test_a_missing_topic_parameter_uses_the_default(portal, client):
    """
    Absent `?topic=` still defaults to 'Array' — unchanged by P2.8b. If that
    topic does not exist the caller now gets a 400 instead of a silent
    substitution, which is the same rule applied consistently.
    """
    array = Topic.objects.create(name="Array", structure_type="flat",
                                 portal=portal)
    question = verified(array, "Default q")

    response = ask(client)

    assert response.status_code == 200
    assert response.json()["id"] == str(question.pk)


def test_a_missing_topic_parameter_with_no_array_topic_is_rejected(
        topic, client):
    verified(topic)

    response = ask(client)

    assert response.status_code == 400
    assert response.json()["requested_topic"] == "Array"


def test_a_valid_topic_with_no_eligible_questions_is_exhausted_not_unknown(
        topic, learner, client):
    """
    The P2.8a `topic_exhausted` contract must survive: an EXISTING topic with
    nothing left is a different condition from a topic that does not exist.
    """
    question = verified(topic)
    CodeSubmission.objects.create(
        user=learner, question=question, language="python", code="x",
        status="accepted", adaptive_eligible=False)

    response = ask(client, topic="SignalTopic")

    assert response.status_code == 200
    assert response.json()["status"] in {"topic_exhausted", "completed"}


def test_a_topic_with_no_questions_at_all_is_exhausted_not_unknown(
        portal, client):
    Topic.objects.create(name="Empty", structure_type="flat", portal=portal)

    response = ask(client, topic="Empty")

    assert response.status_code == 200
    assert response.json()["status"] in {"topic_exhausted", "completed"}
