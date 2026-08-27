"""
The agent orchestrator and its tools (M2 P2.11a).

Local/synthetic. No provider, no key, no network: the planner is a plain
callable, so the loop is testable without an LLM.

What these tests exist to hold: the model chooses which tool runs, and
nothing else. It cannot widen the candidate set, cannot reach an untrusted
question, cannot commit, and cannot prevent the learner getting an answer.
"""

import pytest
from django.contrib.auth import get_user_model

from groups.agent import orchestrator as orch
from groups.agent import tools as toolkit
from groups.models import (CodingPortal, Question, Topic, UserCodingProfile)

User = get_user_model()


@pytest.fixture
def learner(db):
    return User.objects.create_user(username="learner", password="pw",
                                    email="l@example.com")


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Agent Portal")
    row, _ = Topic.objects.get_or_create(
        name="AgentTopic", defaults={"structure_type": "flat",
                                     "portal": portal})
    return row


def make_question(topic, qid, *, trusted=True, title=None):
    return Question.objects.create(
        id=qid, title=title or f"Q{qid}", content=f"<p>Body {qid}</p>",
        topic=topic, base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def f(self): pass\n"},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={},
        status=(Question.STATUS_PUBLISHED if trusted else Question.STATUS_DRAFT),
        trust_state=(Question.TRUST_ORACLE_VERIFIED if trusted
                     else Question.TRUST_UNVERIFIED))


@pytest.fixture
def session(learner):
    return toolkit.Session(user=learner)


def scripted(*plans):
    """A planner that returns each plan in turn, then stalls."""
    queue = list(plans)

    def planner(_observation):
        return queue.pop(0) if queue else {"unusable": True}

    return planner


# ═════════════════════════════════════════════════════════════
# The trust boundary — what the model cannot reach
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_trusted_questions_are_offered(session, topic):
    make_question(topic, 9001, trusted=True)
    make_question(topic, 9002, trusted=False)

    result = toolkit.get_candidate_problems(session)

    ids = [c["question_id"] for c in result["candidates"]]
    assert 9001 in ids
    assert 9002 not in ids, "an unverified question must never be recommended"


@pytest.mark.django_db
def test_a_question_that_was_never_offered_cannot_be_read(session, topic):
    """
    The rule that stops a guessed or hallucinated id reaching a learner. The
    candidate set lives on the session; the model cannot widen it.
    """
    make_question(topic, 9003, trusted=True)

    with pytest.raises(toolkit.ToolDenied, match="not offered"):
        toolkit.get_problem_context(session, question_id=9003)


@pytest.mark.django_db
def test_an_untrusted_question_cannot_be_reached_even_by_id(session, topic):
    make_question(topic, 9004, trusted=False)
    toolkit.get_candidate_problems(session)          # offers nothing

    with pytest.raises(toolkit.ToolDenied):
        toolkit.get_problem_context(session, question_id=9004)


@pytest.mark.django_db
def test_offering_a_question_is_what_makes_it_readable(session, topic):
    make_question(topic, 9005, trusted=True)
    toolkit.get_candidate_problems(session)

    context = toolkit.get_problem_context(session, question_id=9005)
    assert context["question_id"] == 9005


@pytest.mark.django_db
def test_problem_context_never_returns_the_answer_key(session, topic):
    make_question(topic, 9006, trusted=True)
    toolkit.get_candidate_problems(session)

    context = toolkit.get_problem_context(session, question_id=9006)

    assert "hidden_test_cases" not in context
    assert "expected_output" not in str(context)
    assert "hidden_test_cases" in context["withheld"]


@pytest.mark.django_db
def test_tutor_context_returns_only_this_learners_attempts(session, topic,
                                                            db):
    other = User.objects.create_user(username="other", password="pw",
                                     email="o@example.com")
    question = make_question(topic, 9007, trusted=True)
    from groups.models import CodeSubmission
    CodeSubmission.objects.create(user=other, question=question,
                                  language="python", code="x", status="accepted")
    toolkit.get_candidate_problems(session)

    context = toolkit.get_tutor_context(session, question_id=9007)

    assert context["attempt_count"] == 0, "another learner's work leaked"


@pytest.mark.django_db
def test_the_candidate_set_is_capped(session, topic):
    for qid in range(9100, 9140):
        make_question(topic, qid, trusted=True)

    result = toolkit.get_candidate_problems(session, limit=1000)

    assert result["count"] <= session.max_candidates


# ═════════════════════════════════════════════════════════════
# Glicko-2 stays read-only
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_learner_state_reports_the_live_engine_and_the_shadow(session):
    state = toolkit.get_learner_state(session)

    assert state["rating_engine"].startswith("EloEngine")
    assert "UNARMED" in state["glicko_shadow"]


def test_no_tool_writes_glicko_or_kt_state():
    """
    The shadow model is unarmed, and an orchestrator must not arm it as a
    side effect. Asserted against the source so a future edit that reaches
    for `shadow.apply_submission` fails here.
    """
    import inspect

    source = inspect.getsource(toolkit)
    for forbidden in ("shadow.apply_submission", "record_submission_safely",
                      "glicko.rate", "LearnerTopicSkill.objects.create",
                      "GlickoSnapshot"):
        assert forbidden not in source, forbidden


def test_only_one_tool_can_write_at_all():
    writers = [name for name, tool in toolkit.REGISTRY.items()
               if not tool.reads_only]
    assert writers == ["grade_submission"]


# ═════════════════════════════════════════════════════════════
# Argument validation
# ═════════════════════════════════════════════════════════════

def test_unknown_arguments_are_refused():
    tool = toolkit.REGISTRY["get_prerequisites"]
    with pytest.raises(toolkit.ToolError, match="unknown argument"):
        tool.validate({"topic": "Arrays", "sneaky": True})


def test_missing_required_arguments_are_refused():
    tool = toolkit.REGISTRY["get_problem_context"]
    with pytest.raises(toolkit.ToolError, match="missing"):
        tool.validate({})


def test_arguments_must_be_an_object():
    tool = toolkit.REGISTRY["get_learner_state"]
    with pytest.raises(toolkit.ToolError, match="must be an object"):
        tool.validate(["not", "an", "object"])


@pytest.mark.django_db
def test_a_non_integer_question_id_is_refused(session):
    with pytest.raises(toolkit.ToolError, match="must be an integer"):
        toolkit.get_problem_context(session, question_id="'; DROP TABLE--")


# ═════════════════════════════════════════════════════════════
# The loop
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_normal_run_reaches_a_final_answer(session, topic):
    make_question(topic, 9200, trusted=True)
    planner = scripted(
        {"tool": "get_learner_state", "arguments": {}},
        {"tool": "get_candidate_problems", "arguments": {"limit": 5}},
        {"final": "Try Q9200 next."})

    result = orch.Orchestrator(session, planner).run("what next?")

    assert result.stopped_because == "final"
    assert result.answer == "Try Q9200 next."
    assert [c["tool"] for c in result.calls] == [
        "get_learner_state", "get_candidate_problems"]


@pytest.mark.django_db
def test_an_unknown_tool_is_refused_and_fed_back(session, topic):
    make_question(topic, 9201, trusted=True)
    planner = scripted(
        {"tool": "delete_everything", "arguments": {}},
        {"final": "recovered"})

    result = orch.Orchestrator(session, planner).run("go")

    assert result.answer == "recovered"
    assert result.calls[0]["outcome"] == "error"


@pytest.mark.django_db
def test_the_loop_always_terminates(session, topic):
    """A planner that never finishes must not hang the request."""
    make_question(topic, 9202, trusted=True)
    planner = scripted(*[{"tool": "get_learner_state", "arguments": {}}] * 50)

    result = orch.Orchestrator(session, planner, max_tool_calls=4).run("go")

    assert result.stopped_because == "max_tool_calls"
    assert len(result.calls) == 4


@pytest.mark.django_db
def test_a_timeout_stops_the_loop(session, topic):
    make_question(topic, 9203, trusted=True)
    ticks = iter([0, 0, 99, 99, 99, 99])
    planner = scripted(*[{"tool": "get_learner_state", "arguments": {}}] * 5)

    result = orch.Orchestrator(session, planner, timeout_seconds=1,
                               clock=lambda: next(ticks)).run("go")

    assert result.stopped_because == "timeout"


@pytest.mark.django_db
def test_repeated_tool_errors_stop_the_loop(session, topic):
    make_question(topic, 9204, trusted=True)
    planner = scripted(*[{"tool": "nope", "arguments": {}}] * 6)

    result = orch.Orchestrator(session, planner).run("go")

    assert result.stopped_because == "too_many_tool_errors"


@pytest.mark.django_db
def test_a_denial_stops_immediately_and_is_never_retried(session, topic):
    make_question(topic, 9205, trusted=True)
    planner = scripted(
        {"tool": "get_problem_context", "arguments": {"question_id": 9205}},
        {"final": "should not be reached"})

    result = orch.Orchestrator(session, planner).run("go")

    assert result.stopped_because == "denied"
    assert result.answer != "should not be reached"


@pytest.mark.django_db
def test_the_fallback_still_answers(session, topic):
    """However the loop gives up, the learner gets something correct."""
    make_question(topic, 9206, trusted=True, title="Fallback Problem")
    UserCodingProfile.objects.create(user=session.user, elo_rating=1234.0)
    planner = scripted()          # unusable from the first call

    result = orch.Orchestrator(session, planner).run("go")

    assert "Fallback Problem" in result.answer
    assert "1234" in result.answer


# ═════════════════════════════════════════════════════════════
# commit, and what the UI is shown
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_model_cannot_commit(session, topic, monkeypatch):
    """
    `commit` is the orchestrator's word. A planner that asks for it gets it
    stripped, so the escalation cannot be expressed by the transport.
    """
    make_question(topic, 9207, trusted=True)
    seen = {}

    def spy(sess, **kwargs):
        seen.update(kwargs)
        return {"persisted": False}

    monkeypatch.setitem(
        toolkit.REGISTRY, "grade_submission",
        toolkit.Tool("grade_submission", spy, False, "spy",
                     required=("question_id", "language", "source")))

    planner = scripted(
        {"tool": "get_candidate_problems", "arguments": {}},
        {"tool": "grade_submission",
         "arguments": {"question_id": 9207, "language": "python",
                       "source": "x = 1", "commit": True}},
        {"final": "done"})

    orch.Orchestrator(session, planner).run("grade this")

    assert "commit" not in seen, "the model escalated to a write"


@pytest.mark.django_db
def test_grading_does_not_persist_without_an_explicit_commit(session, topic,
                                                             monkeypatch):
    from groups.models import CodeSubmission

    make_question(topic, 9208, trusted=True)
    toolkit.get_candidate_problems(session)

    class FakeGrade:
        all_passed = True
        final_status = "accepted"
        stored_code = "x"
        results = []

    monkeypatch.setattr("groups.services.GradingService.grade",
                        lambda self, q, l, s: FakeGrade())

    # A runner is injected rather than defaulted, so no Judge0 call happens.
    result = toolkit.grade_submission(
        session, question_id=9208, language="python", source="x = 1",
        runner=lambda *a, **k: {"status_id": 3, "stdout": "1"})

    assert result["persisted"] is False
    assert not CodeSubmission.objects.filter(user=session.user).exists()


@pytest.mark.django_db
def test_the_transcript_is_actions_not_reasoning(session, topic):
    make_question(topic, 9209, trusted=True)
    planner = scripted(
        {"tool": "get_learner_state", "arguments": {},
         "reasoning": "SECRET internal deliberation the UI must never see"},
        {"final": "done"})

    result = orch.Orchestrator(session, planner).run("go")

    assert result.transcript == ["Checking learner state"]
    assert "SECRET" not in str(result.as_dict())


def test_every_tool_has_a_narration():
    assert set(toolkit.NARRATION) == set(toolkit.REGISTRY)
    for phrase in toolkit.NARRATION.values():
        assert phrase[0].isupper() and len(phrase) < 60


@pytest.mark.django_db
def test_logged_arguments_never_carry_source_code(session, topic):
    make_question(topic, 9210, trusted=True)
    long_source = "print('x')\n" * 200
    summary = orch._loggable({"source": long_source, "question_id": 9210})

    assert "print" not in str(summary)
    assert summary["question_id"] == 9210
