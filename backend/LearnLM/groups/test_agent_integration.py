"""
The agent, wired to real services (M2 P2.14 §24H).

The P2.11a suite proved the loop in isolation. This one proves the seams:
an authenticated endpoint, a real provider abstraction, real tools, and the
three-layer fallback that has to hold when any of them fails.

No network and no API key. The provider is injected at the one seam
`llm_planner` exposes, so every failure mode — quota exhausted, unreachable,
unparseable, hallucinating — is exercised deterministically.
"""

import json
import logging

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from groups.agent import kt_signal, provider
from groups.agent import tools as toolkit
from groups.agent.orchestrator import Orchestrator
from groups.models import CodingPortal, Question, Topic

User = get_user_model()
AGENT_URL = "/api/ai/agent/"


# ═════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def learner(db):
    return User.objects.create_user(username="agent-learner", password="pw",
                                    email="agent@example.com")


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Agent Integration Portal")
    row, _ = Topic.objects.get_or_create(
        name="AgentIntegrationTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return row


@pytest.fixture
def trusted_question(topic):
    return Question.objects.create(
        id=91001, title="Trusted Practice Problem",
        content="<p>Add two numbers.</p>", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def f(self): pass\n"},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={},
        status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED)


@pytest.fixture
def client(learner):
    api = APIClient()
    api.force_authenticate(user=learner)
    return api


@pytest.fixture
def agent_on(settings):
    settings.AGENT_ORCHESTRATOR_ENABLED = True
    return settings


#: Captured at import, BEFORE any test patches the name.
#:
#: `monkeypatch.setattr(provider, "llm_planner", lambda: provider.llm_planner(...))`
#: recurses forever — the lambda calls the very name it just replaced. Holding
#: the original here is what makes the injection seam usable.
REAL_LLM_PLANNER = provider.llm_planner


def scripted_provider(*payloads):
    """A stand-in for `_generate_json_with_fallback`."""
    queue = list(payloads)

    def generate(_prompt, _label):
        return queue.pop(0) if queue else None

    return generate


def scripted_planner(*plans):
    """
    A planner callable for driving `Orchestrator` directly.

    `scripted_provider` stands in for the raw JSON generator; this is one
    level up — the thing `Orchestrator` actually calls. Repeats the last
    plan once exhausted so a loop that asks again does not IndexError.
    """
    queue = list(plans)

    def plan(_observation):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return plan


def exploding_provider(exception):
    def generate(_prompt, _label):
        raise exception

    return generate


def use_provider(monkeypatch, generate):
    """Point the endpoint at a scripted provider."""
    monkeypatch.setattr(provider, "llm_planner",
                        lambda: REAL_LLM_PLANNER(generate=generate))


# ═════════════════════════════════════════════════════════════
# Endpoint authentication (§24H)
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_endpoint_refuses_an_anonymous_caller():
    response = APIClient().post(AGENT_URL, {"request": "what next?"},
                                format="json")
    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_an_authenticated_learner_is_served(client, trusted_question):
    response = client.post(AGENT_URL, {"request": "What should I practise?"},
                           format="json")

    assert response.status_code == 200
    assert response.data["answer"]


@pytest.mark.django_db
def test_a_missing_request_field_is_a_client_error(client):
    assert client.post(AGENT_URL, {}, format="json").status_code == 400


@pytest.mark.django_db
def test_a_blank_request_is_refused(client):
    assert client.post(AGENT_URL, {"request": "   "},
                       format="json").status_code == 400


@pytest.mark.django_db
def test_an_oversized_request_is_refused(client):
    """The prompt is not a place to post a payload."""
    response = client.post(
        AGENT_URL, {"request": "x" * (provider.MAX_REQUEST_CHARS + 1)
                    if hasattr(provider, "MAX_REQUEST_CHARS")
                    else "x" * 5000},
        format="json")
    assert response.status_code == 400


# ═════════════════════════════════════════════════════════════
# The flag, and the deterministic floor (§24G)
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_with_the_flag_off_the_deterministic_recommender_answers(
        client, trusted_question, settings):
    settings.AGENT_ORCHESTRATOR_ENABLED = False

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert response.data["source"] == "deterministic"
    assert response.data["stopped_because"] == "agent_disabled"
    assert trusted_question.title in response.data["answer"]


@pytest.mark.django_db
def test_the_learner_still_gets_a_recommendation_when_the_provider_dies(
        client, trusted_question, agent_on, monkeypatch):
    """
    §24G: quota exhausted, provider down, whatever — a learner asking what to
    practise must still be told what to practise.
    """
    from groups.ai_services import DailyQuotaExhausted

    use_provider(monkeypatch, exploding_provider(DailyQuotaExhausted("out for today")))

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert response.status_code == 200
    assert response.data["source"] == "agent_fallback"
    assert trusted_question.title in response.data["answer"]


@pytest.mark.django_db
def test_an_unparseable_provider_response_falls_back(
        client, trusted_question, agent_on, monkeypatch):
    """`_generate_json_with_fallback` returns None for unparseable output."""
    use_provider(monkeypatch, scripted_provider(None))

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert response.data["source"] == "agent_fallback"
    assert response.data["answer"]


@pytest.mark.django_db
def test_a_null_final_never_reaches_the_learner_as_the_word_none(
        client, trusted_question, agent_on, monkeypatch):
    """
    The orchestrator stringifies `final`. A null there would have handed the
    learner the literal text "None"; it must fall back instead.
    """
    use_provider(monkeypatch, scripted_provider({"final": None}))

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert response.data["answer"] != "None"
    assert "None" not in response.data["answer"]


@pytest.mark.django_db
def test_an_orchestrator_crash_still_answers(client, trusted_question,
                                             agent_on, monkeypatch):
    def explode():
        raise RuntimeError("loop blew up outside its own guards")

    monkeypatch.setattr(provider, "llm_planner", explode)

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert response.status_code == 200
    assert response.data["source"] == "deterministic"
    assert response.data["stopped_because"] == "orchestrator_error"


# ═════════════════════════════════════════════════════════════
# Real tools, through the endpoint (§24C)
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_agent_reaches_a_recommendation_through_real_tools(
        client, trusted_question, agent_on, monkeypatch):
    use_provider(monkeypatch, scripted_provider(
            {"tool": "get_learner_state", "arguments": {},
             "reasoning": "check standing"},
            {"tool": "get_candidate_problems", "arguments": {"limit": 5},
             "reasoning": "list what is servable"},
            {"final": "Try Trusted Practice Problem — it matches your level.",
             "reasoning": "pick one"},
    ))

    response = client.post(AGENT_URL, {"request": "What should I practise?"},
                           format="json")

    assert response.data["source"] == "agent"
    assert response.data["stopped_because"] == "final"
    assert [c["tool"] for c in response.data["tool_calls"]] == [
        "get_learner_state", "get_candidate_problems"]


@pytest.mark.django_db
def test_a_hallucinated_question_id_cannot_reach_a_learner(
        client, trusted_question, agent_on, monkeypatch):
    """
    §24C: the candidate set is server-controlled. A question id the backend
    never offered must be refused, not fetched.
    """
    use_provider(monkeypatch, scripted_provider(
            {"tool": "get_problem_context", "arguments": {"question_id": 4242},
             "reasoning": "made this up"},
    ))

    response = client.post(AGENT_URL, {"request": "give me problem 4242"},
                           format="json")

    assert response.data["stopped_because"] == "denied"
    assert "4242" not in json.dumps(response.data["answer"])


@pytest.mark.django_db
def test_an_untrusted_question_is_never_offered_through_the_endpoint(
        client, topic, agent_on, monkeypatch):
    Question.objects.create(
        id=91002, title="Unverified Problem", content="<p>x</p>", topic=topic,
        base_difficulty=1200.0, boilerplate_code={}, hidden_test_cases=[],
        hidden_wrapper_code={}, status=Question.STATUS_DRAFT,
        trust_state=Question.TRUST_UNVERIFIED)

    use_provider(monkeypatch, scripted_provider(
            {"tool": "get_candidate_problems", "arguments": {}},
            {"final": "done"},
    ))

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert "Unverified Problem" not in json.dumps(response.data)


@pytest.mark.django_db
def test_invalid_tool_arguments_are_refused_and_retried(
        client, trusted_question, agent_on, monkeypatch):
    use_provider(monkeypatch, scripted_provider(
            {"tool": "get_prerequisites", "arguments": {"sneaky": True}},
            {"final": "Recovered and answered."},
    ))

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert response.data["source"] == "agent"
    assert any(c["outcome"] == "error" for c in response.data["tool_calls"])


@pytest.mark.django_db
def test_an_unknown_tool_does_not_crash_the_request(
        client, trusted_question, agent_on, monkeypatch):
    use_provider(monkeypatch, scripted_provider(
            {"tool": "drop_all_tables", "arguments": {}},
            {"final": "Recovered."},
    ))

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert response.status_code == 200


@pytest.mark.django_db
def test_the_loop_terminates_at_the_call_cap(client, trusted_question,
                                             agent_on, monkeypatch):
    """A planner that never finishes must still leave the learner served."""
    def never_finishes(_prompt, _label):
        return {"tool": "get_learner_state", "arguments": {}}

    use_provider(monkeypatch, never_finishes)

    response = client.post(AGENT_URL, {"request": "loop forever"},
                           format="json")

    assert response.data["stopped_because"] == "max_tool_calls"
    assert len(response.data["tool_calls"]) <= 8
    assert response.data["answer"]


@pytest.mark.django_db
def test_a_timeout_stops_the_loop(learner, trusted_question):
    """Wall-clock, checked before each call, with an injected clock."""
    ticks = iter([0.0, 0.0, 999.0, 999.0, 999.0])
    session = toolkit.Session(user=learner)

    result = Orchestrator(
        session,
        lambda _o: {"tool": "get_learner_state", "arguments": {}},
        clock=lambda: next(ticks)).run("what next?")

    assert result.stopped_because == "timeout"
    assert result.answer


@pytest.mark.django_db
def test_the_model_cannot_commit_a_graded_submission(
        client, trusted_question, agent_on, monkeypatch):
    """
    §24J: no arbitrary DB mutation. `commit` is the orchestrator's word, and
    a model asking for it gets it stripped rather than honoured.
    """
    from groups.models import CodeSubmission

    use_provider(monkeypatch, scripted_provider(
            {"tool": "get_candidate_problems", "arguments": {}},
            {"tool": "grade_submission",
             "arguments": {"question_id": trusted_question.pk,
                           "language": "python", "source": "print(1)",
                           "commit": True}},
            {"final": "done"},
    ))
    monkeypatch.setattr(
        "groups.services.GradingService.grade",
        lambda self, *a, **k: type("G", (), {
            "all_passed": True, "final_status": "accepted",
            "results": [], "passed": 1, "total": 1})())

    before = CodeSubmission.objects.count()
    client.post(AGENT_URL, {"request": "grade this"}, format="json")

    assert CodeSubmission.objects.count() == before, (
        "the model persisted a submission")


# ═════════════════════════════════════════════════════════════
# No hidden chain-of-thought (§24F)
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_planners_reasoning_never_reaches_the_response(
        client, trusted_question, agent_on, monkeypatch):
    secret = "INTERNAL-REASONING-SHOULD-NOT-APPEAR"

    use_provider(monkeypatch, scripted_provider(
            {"tool": "get_learner_state", "arguments": {}, "reasoning": secret},
            {"final": "Try the trusted problem.", "reasoning": secret},
    ))

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert secret not in json.dumps(response.data, default=str)


@pytest.mark.django_db
def test_the_transcript_is_narration_and_not_reasoning(
        client, trusted_question, agent_on, monkeypatch):
    use_provider(monkeypatch, scripted_provider(
            {"tool": "get_learner_state", "arguments": {}},
            {"final": "Answered."},
    ))

    response = client.post(AGENT_URL, {"request": "what next?"},
                           format="json")

    assert response.data["transcript"] == ["Checking learner state"]


@pytest.mark.django_db
def test_no_answer_key_leaves_the_endpoint(client, trusted_question,
                                           agent_on, monkeypatch):
    use_provider(monkeypatch, scripted_provider(
            {"tool": "get_candidate_problems", "arguments": {}},
            {"tool": "get_problem_context",
             "arguments": {"question_id": trusted_question.pk}},
            {"final": "Here is the problem."},
    ))

    response = client.post(AGENT_URL, {"request": "show me a problem"},
                           format="json")
    body = json.dumps(response.data, default=str)

    assert "expected_output" not in body
    assert "hidden_test_cases" not in body


# ═════════════════════════════════════════════════════════════
# Glicko-2 stays a read signal (§24D)
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_glicko_is_reported_but_elo_remains_the_engine(learner):
    session = toolkit.Session(user=learner)

    state = toolkit.get_learner_state(session)

    assert state["rating_engine"].startswith("EloEngine")
    assert "glicko_readings" in state
    assert isinstance(state["glicko_readings"], list)


@pytest.mark.django_db
def test_reading_glicko_does_not_write_learner_skill_rows(learner, topic):
    """
    `current_ability` inflates RD for the caller and persists nothing —
    merely looking at a learner must not age them.
    """
    from groups.models import LearnerTopicSkill

    session = toolkit.Session(user=learner)
    before = LearnerTopicSkill.objects.count()

    toolkit.get_learner_state(session)
    toolkit.get_learner_state(session)

    assert LearnerTopicSkill.objects.count() == before


def strip_docstrings(module):
    """Module source with every docstring removed, for code-only checks."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                body.pop(0)
                if not body:
                    body.append(ast.Pass())
    return ast.unparse(tree)


def test_no_agent_module_arms_the_shadow_model():
    """
    Asserted against the code, so a future edit reaching for the write path
    fails here rather than in production.

    Docstrings are stripped first: these modules document at length that they
    do NOT call `record_submission_safely`, and that sentence must not be
    mistaken for the call itself.
    """
    from groups.agent import kt_signal as kt
    from groups.agent import provider as prov
    from groups.agent import views as agent_views

    # `shadow.apply_submission` specifically, NOT bare `apply_submission`:
    # `ProgressionService.apply_submission` is the production Elo commit and
    # is exactly what `grade_submission` is supposed to call when the
    # orchestrator — never the model — asks it to.
    for module in (toolkit, prov, kt, agent_views):
        code = strip_docstrings(module)
        for forbidden in ("shadow.apply_submission",
                          "shadow.record_submission_safely",
                          "record_submission_safely(",
                          "glicko.rate", "LearnerTopicSkill.objects.create",
                          "LearnerTopicSkill.objects.update"):
            assert forbidden not in code, f"{module.__name__}: {forbidden}"


@pytest.mark.django_db
def test_only_the_orchestrator_can_persist_and_only_deliberately(learner,
                                                                 topic):
    """
    The counterpart to the check above: `grade_submission` CAN write, but
    only when the caller passes `commit=True` — and the orchestrator strips
    that key from every model-supplied payload.
    """
    from groups.models import CodeSubmission

    question = Question.objects.create(
        id=91009, title="Commit Probe", content="<p>x</p>", topic=topic,
        base_difficulty=1200.0, boilerplate_code={},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED)

    session = toolkit.Session(user=learner)
    toolkit.get_candidate_problems(session)
    before = CodeSubmission.objects.count()

    graded = toolkit.grade_submission(
        session, question_id=question.pk, language="python",
        source="print(1)", runner=lambda *a, **k: {"stdout": "1",
                                                   "status": "Accepted"})

    assert graded["persisted"] is False
    assert CodeSubmission.objects.count() == before


# ═════════════════════════════════════════════════════════════
# TA-GTKT stays a read signal (§24E)
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_with_no_export_the_kt_signal_says_unavailable(learner, settings):
    settings.KT_PREDICTION_EXPORT = ""

    reading = kt_signal.predict(learner)

    assert reading["status"] == kt_signal.UNAVAILABLE
    assert reading["predicted_mastery"] is None
    assert reading["predicted_next_correct"] is None
    assert "KT_PREDICTION_EXPORT" in reading["reason"]


@pytest.mark.django_db
def test_an_unreadable_export_degrades_rather_than_raising(learner, settings,
                                                           tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    settings.KT_PREDICTION_EXPORT = str(broken)

    reading = kt_signal.predict(learner)

    assert reading["status"] == kt_signal.UNAVAILABLE


@pytest.mark.django_db
def test_an_export_is_read_and_carries_model_metadata(learner, settings,
                                                      tmp_path):
    export = tmp_path / "kt.json"
    export.write_text(json.dumps({
        "model": "TA-GTKT",
        "model_version": "p2.13/v1",
        "trained_on": "ASSISTments 2009-2010 skill builder",
        "dataset_fingerprint": "423bcaa2cfe60600",
        "exported_at": "2026-08-29T00:00:00Z",
        "learners": {str(learner.pk): {"predicted_mastery": 0.62,
                                       "predicted_next_correct": 0.71}},
    }), encoding="utf-8")
    settings.KT_PREDICTION_EXPORT = str(export)

    reading = kt_signal.predict(learner)

    assert reading["status"] == kt_signal.AVAILABLE
    assert reading["predicted_mastery"] == 0.62
    assert reading["predicted_next_correct"] == 0.71
    assert reading["model"] == "TA-GTKT"
    assert reading["model_version"] == "p2.13/v1"


@pytest.mark.django_db
def test_every_kt_reading_states_it_never_saw_a_sparklm_learner(
        learner, settings, tmp_path):
    """
    §24E: do not pretend the model is production trained on SparkLM learners.
    The caveat travels with the number, present or absent.
    """
    settings.KT_PREDICTION_EXPORT = ""
    assert "never seen a SparkLM learner" in kt_signal.predict(
        learner)["applicability"]

    export = tmp_path / "kt.json"
    export.write_text(json.dumps({
        "model": "TA-GTKT", "trained_on": "ASSISTments",
        "learners": {str(learner.pk): {"predicted_mastery": 0.5,
                                       "predicted_next_correct": 0.5}},
    }), encoding="utf-8")
    settings.KT_PREDICTION_EXPORT = str(export)

    assert "never seen a SparkLM learner" in kt_signal.predict(
        learner)["applicability"]


def test_the_kt_signal_never_imports_the_research_package():
    """
    The web tier has no tensor framework by design (M1 P1.1). A production
    module importing `kt_research` would put it back and make a research
    checkpoint a deployment dependency.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(kt_signal))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert name.split(".")[0] not in {"kt_research", "torch"}, name


# ═════════════════════════════════════════════════════════════
# Provider abstraction (§24A)
# ═════════════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════════════
# The demo scenario (§24I)
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_demo_runs_end_to_end_and_writes_nothing(learner,
                                                     trusted_question):
    """
    §24I: one deterministic scenario, no provider, no Oracle, no publication
    step — and no state change.
    """
    import io

    from django.core.management import call_command
    from groups.models import CodeSubmission

    before = CodeSubmission.objects.count()
    out = io.StringIO()

    call_command("agent_demo", "--user", learner.username, stdout=out)
    printed = out.getvalue()

    assert CodeSubmission.objects.count() == before
    assert "WROTE NOTHING" in printed
    assert trusted_question.title in printed
    assert "Elo decides what is served" in printed


@pytest.mark.django_db
def test_the_demo_names_only_a_question_the_backend_offered(learner,
                                                            trusted_question):
    import io

    from django.core.management import call_command

    out = io.StringIO()
    call_command("agent_demo", "--user", learner.username, "--json",
                 stdout=out)
    payload = json.loads(out.getvalue())

    assert payload["wrote_nothing"] is True
    named = [c for c in payload["result"]["tool_calls"]
             if c["tool"] == "get_problem_context"]
    for call in named:
        assert call["arguments"]["question_id"] == trusted_question.pk


@pytest.mark.django_db
def test_the_demo_survives_an_empty_trusted_bank(learner, topic):
    """
    Two of 2,926 questions are trusted today. An empty candidate set is a
    real state the agent has to handle, not a failure to hide.
    """
    import io

    from django.core.management import call_command

    out = io.StringIO()
    call_command("agent_demo", "--user", learner.username, stdout=out)

    assert "no verified problem" in out.getvalue().lower()


@pytest.mark.django_db
def test_the_demo_refuses_an_unknown_user():
    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError, match="no such user"):
        call_command("agent_demo", "--user", "nobody-by-that-name")


def test_the_planner_uses_the_configured_model_not_a_hard_coded_one():
    """
    §24A found `ai_services._generate_json_with_fallback` hard-codes
    `llama-3.3-70b-versatile`, which has been WITHDRAWN — Groq 404s and the
    NIM backup only fires on a quota error, so it returns None on every call.
    An agent built on it would fall back forever with every test green.

    The model id must therefore come from the configured registry that
    `probe_provider` can verify by listing.
    """
    code = strip_docstrings(provider)

    assert "PROVIDER_MODELS" in code
    assert "llama-3.3-70b-versatile" not in code
    assert "_generate_json_with_fallback" not in code


def test_the_planner_reuses_the_existing_quota_classifier_and_backup():
    """Reused, not reimplemented: one set of quota semantics, not two."""
    code = strip_docstrings(provider)

    assert "_is_exhausted" in code
    assert "_call_nim_raw" in code


@pytest.mark.django_db
def test_a_withdrawn_model_is_reported_rather_than_silently_falling_back():
    """
    The failure that started this: a 404 must be visible. `probe()` lists
    models and says whether the configured one is offered, without spending
    a generation call to find out.
    """
    report = provider.probe()

    assert set(report) >= {"provider", "configured", "reachable", "models"}
    assert report["configured"], "no model is configured for the agent"


def test_the_agent_path_does_not_call_gemini():
    """
    Gemini's free tier is 20 requests/day and has been exhausted before.
    Exhausting it must not disable the agent, and running the agent must not
    consume the question-generation budget.

    Checked on the CODE, with docstrings stripped — this module has to be
    able to explain why it avoids Gemini without that explanation reading as
    evidence it uses it.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(provider))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                body.pop(0)
                if not body:
                    body.append(ast.Pass())

    # The CALL surfaces, not the word. `_safe` names GEMINI_API_KEY in its
    # redaction list, which is defence in depth rather than a call — a test
    # that banned the word would punish the safer code.
    code = ast.unparse(tree).lower()
    for forbidden in ("genai", "generativemodel", "generate_content",
                      "google.generativeai"):
        assert forbidden not in code, forbidden

    assert "gemini_api_key" in code, (
        "sanity: the key name should still appear, in the redaction list")


def test_the_agent_reads_configuration_through_settings_not_the_environment():
    """
    Config goes through `django.conf.settings`, which is the project's one
    place for it. A module reaching into `os.environ` bypasses the feature-flag
    register that `test_feature_flags` enforces.
    """
    from groups.agent import views as agent_views

    for module in (provider, agent_views, toolkit):
        code = strip_docstrings(module)
        for forbidden in ("os.getenv", "os.environ"):
            assert forbidden not in code, f"{module.__name__}: {forbidden}"


@pytest.mark.django_db
def test_a_credential_never_reaches_the_response_or_the_log(
        client, trusted_question, agent_on, monkeypatch, settings, caplog):
    """
    The property that matters. Constructing a provider client requires the
    key — that is unavoidable and correct. What must never happen is the key
    reaching a learner or a log line.
    """
    sentinel = "sk-SENTINEL-DO-NOT-LEAK-0123456789"
    settings.GROQ_API_KEY = sentinel

    def leaky_provider(_prompt, _label):
        raise RuntimeError(f"upstream rejected key {sentinel}")

    use_provider(monkeypatch, leaky_provider)

    with caplog.at_level("DEBUG"):
        response = client.post(AGENT_URL, {"request": "what next?"},
                               format="json")

    assert response.status_code == 200
    assert sentinel not in json.dumps(response.data, default=str)
    assert sentinel not in caplog.text, (
        "a credential reached the log — check for exc_info on a provider error")
    assert "redacted" in caplog.text


def test_the_prompt_lists_only_real_tools():
    """A menu naming a tool that does not exist teaches the model to fail."""
    prompt = provider.build_prompt({"request": "hi", "results": []})

    for name in toolkit.REGISTRY:
        assert name in prompt
    assert "drop_all_tables" not in prompt


def test_the_prompt_forbids_inventing_a_question_id():
    prompt = provider.build_prompt({"request": "hi", "results": []})
    assert "question_id" in prompt and "Inventing one is refused" in prompt


def test_a_long_tool_result_is_truncated_before_it_reaches_the_prompt():
    observation = {"request": "hi",
                   "results": [{"tool": "get_problem_context",
                                "result": {"statement": "x" * 40_000}}]}

    prompt = provider.build_prompt(observation)

    assert len(prompt) < 6_000
    assert "truncated" in prompt


# ═════════════════════════════════════════════════════════════════════
# The ANSWER boundary (M2 P2.23)
#
# `require_offered` guards tool CALLS. These cover the second way the model
# reaches a learner: the question id it names in its own answer.
# ═════════════════════════════════════════════════════════════════════

def test_a_recommendation_the_backend_never_offered_is_rejected(learner):
    """The whole plan is refused, not just the id — and the floor answers."""
    session = toolkit.Session(user=learner)
    planner = scripted_planner(
        {"final": "Try question 424242.", "recommend": 424242})

    result = Orchestrator(session, planner).run("what next?")

    assert result.stopped_because == "recommendation_rejected"
    assert result.recommendation is None
    assert "424242" not in result.answer


def test_a_recommendation_the_backend_did_offer_is_validated_and_returned(
        learner, trusted_question):
    session = toolkit.Session(user=learner)
    toolkit.get_candidate_problems(session)
    planner = scripted_planner(
        {"final": "Try this one.", "recommend": trusted_question.pk})

    result = Orchestrator(session, planner).run("what next?")

    assert result.stopped_because == "final"
    assert result.recommendation["question_id"] == trusted_question.pk
    assert result.recommendation["validated_by"] == "backend"


def test_a_question_demoted_after_being_offered_is_refused_at_the_answer(
        learner, trusted_question):
    """
    The offered set records what WAS offered. Trust can change underneath it,
    so membership alone is not sufficient — this is why the validator
    re-reads the row instead of trusting the set.
    """
    session = toolkit.Session(user=learner)
    toolkit.get_candidate_problems(session)

    trusted_question.trust_state = Question.TRUST_UNVERIFIED
    trusted_question.save(update_fields=["trust_state"])

    with pytest.raises(toolkit.RecommendationRejected):
        toolkit.validate_recommendation(session, trusted_question.pk)


def test_a_non_integer_recommendation_is_refused(learner):
    session = toolkit.Session(user=learner)
    with pytest.raises(toolkit.RecommendationRejected):
        toolkit.validate_recommendation(session, "DROP TABLE questions")


def test_the_deterministic_layer_returns_the_same_validated_shape(
        client, trusted_question, settings):
    """A caller must not be able to tell which layer answered by shape."""
    settings.AGENT_ORCHESTRATOR_ENABLED = False

    response = client.post("/api/ai/agent/", {"request": "what next?"},
                           format="json")

    assert response.status_code == 200
    assert "recommendation" in response.data
    assert response.data["recommendation"]["validated_by"] == "backend"
    assert response.data["source"] == "deterministic"


def test_the_prompt_teaches_the_recommend_field(learner):
    prompt = provider.build_prompt({"request": "hi", "results": []})
    assert '"recommend"' in prompt
    assert "backend re-checks" in prompt


def test_the_decision_log_carries_no_reasoning(learner, trusted_question,
                                               caplog):
    """
    Chain-of-thought must not reach the log. The planner's `reasoning` is
    kept in `_private`; the structured line must never quote it.
    """
    session = toolkit.Session(user=learner)
    toolkit.get_candidate_problems(session)
    secret_reasoning = "INTERNAL-CHAIN-OF-THOUGHT-SENTINEL"
    planner = scripted_planner(
        {"final": "Try this one.", "recommend": trusted_question.pk,
         "reasoning": secret_reasoning})

    with caplog.at_level(logging.INFO):
        Orchestrator(session, planner).run("what next?")

    assert "agent decision" in caplog.text
    assert secret_reasoning not in caplog.text


def test_the_decision_log_is_structured_and_parseable(learner, caplog):
    session = toolkit.Session(user=learner)
    planner = scripted_planner({"final": "No recommendation.",
                                "recommend": None})

    with caplog.at_level(logging.INFO):
        Orchestrator(session, planner).run("hello")

    line = [r for r in caplog.records if "agent decision" in r.getMessage()][0]
    payload = json.loads(line.getMessage().split("agent decision ", 1)[1])
    assert payload["outcome"] == "final"
    assert payload["selected_question_id"] is None
    assert payload["latency_ms"] is not None
    assert set(payload) >= {"request_id", "learner_id", "tools_invoked",
                            "candidates_offered", "rejected_recommendation"}


def test_the_dag_signal_reads_the_production_graph(learner, topic):
    """
    §11: the prerequisite signal must come from the production curriculum
    graph, not a second traversal built inside the agent. A topic absent
    from every graph returns an explicit empty answer rather than raising.
    """
    session = toolkit.Session(user=learner)

    signal = toolkit.get_prerequisites(session, topic.name)

    assert signal["topic"] == topic.name
    assert "prerequisites" in signal and "unlocks" in signal
    assert "read-only" in signal["source"] or "not present" in signal["source"]


def test_an_unknown_topic_returns_an_empty_dag_signal_not_an_error(learner):
    session = toolkit.Session(user=learner)

    signal = toolkit.get_prerequisites(session, "NoSuchTopicAnywhere")

    assert signal["subject"] is None
    assert signal["prerequisites"] == []
    assert "not present" in signal["source"]


def test_the_dag_signal_needs_a_topic(learner):
    session = toolkit.Session(user=learner)
    with pytest.raises(toolkit.ToolError):
        toolkit.get_prerequisites(session, "")
