"""
The direct-id serving boundary (M2 P2.7h-13).

`_servable_questions()` has always quarantined placeholder rows and rows with
no hidden tests from every recommendation path. The direct-id endpoints did
not use it: `CodeRunView` and `CodeSubmitView` re-fetched with a bare
`Question.objects.get(id=...)`, so knowing an integer was enough to reach a
question the selector deliberately excludes — Run executed its wrapper, Submit
graded against its hidden tests and wrote a `CodeSubmission`.

These tests hold one property: **the direct-id path may not execute anything
the recommendation path would not offer.**

Deliberately NOT asserted here: `status == PUBLISHED` or
`trust_state == ORACLE_VERIFIED`. That is the open serving-policy decision
(P2.7h-12) and this phase does not make it — these tests must keep passing
whichever way it goes, so they assert the predicate is SHARED, not what the
predicate contains.
"""

import json

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from groups import coding_views
from groups.models import CodeSubmission, CodingPortal, Question, Topic

SOLUTION = ("class Solution:\n"
            "    def solve(self, n: int) -> int:\n"
            "        return n\n")
CASES = [{"stdin": "1", "expected_output": "1"},
         {"stdin": "2", "expected_output": "2"}]


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(
        username="boundary-learner", password="pw", email="b@example.com")


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Boundary Portal")
    row, _ = Topic.objects.get_or_create(
        name="BoundaryTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return row


def make_question(topic, **overrides):
    fields = dict(
        title="Boundary subject", content="A real statement.", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n"
                                    "    def solve(self, n: int): pass\n"},
        hidden_test_cases=json.loads(json.dumps(CASES)),
        hidden_wrapper_code={}, execution_contract_version="v3")
    fields.update(overrides)
    return Question.objects.create(**fields)


@pytest.fixture
def servable(topic):
    return make_question(topic)


@pytest.fixture
def placeholder(topic):
    """Excluded by the FIRST exclusion: a templated, never-written statement."""
    return make_question(
        topic,
        content=f"<p>{Question.PLACEHOLDER_MARKER} Some Challenge</p>")


@pytest.fixture
def empty_suite(topic):
    """Excluded by the SECOND exclusion."""
    return make_question(topic, hidden_test_cases=[])


def judge0_spy(sink, stdout="1"):
    def runner(source, language, stdin=""):
        sink.setdefault("calls", []).append(
            {"source": source, "language": language, "stdin": stdin})
        return {"status": "Accepted", "status_id": 3, "stdout": stdout,
                "stderr": "", "compile_output": "", "time": "0.01",
                "memory": 512}
    return runner


def submit(client, question_id, code=SOLUTION):
    return client.post(reverse("code-submit"), {
        "problem_id": question_id, "code": code, "language": "python",
    }, format="json")


def run(client, question_id, code=SOLUTION):
    return client.post(reverse("code-run"), {
        "problem_id": question_id, "code": code, "language": "python",
        "stdin": "1",
    }, format="json")


# ═════════════════════════════════════════════════════════════
# The predicate has one definition
# ═════════════════════════════════════════════════════════════

def test_the_direct_id_helper_is_derived_from_the_selector():
    """
    Structural: restating the exclusions is how the two paths came to
    disagree. `_servable_question` must call `_servable_questions`, not
    re-implement it.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(coding_views._servable_question))
    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "_servable_questions" in called

    source = inspect.getsource(coding_views._servable_question)
    assert "PLACEHOLDER_MARKER" not in source
    assert "hidden_test_cases" not in source


def test_neither_view_re_implements_the_exclusions():
    import inspect

    for view in (coding_views.CodeRunView.post, coding_views.CodeSubmitView.post):
        source = inspect.getsource(view)
        assert "PLACEHOLDER_MARKER" not in source
        assert "exclude(" not in source


@pytest.mark.django_db
@pytest.mark.parametrize("fixture_name", ["placeholder", "empty_suite"])
def test_the_helper_refuses_everything_the_selector_excludes(
        fixture_name, request, servable):
    excluded = request.getfixturevalue(fixture_name)
    assert coding_views._servable_question(excluded.pk) is None
    assert coding_views._servable_question(servable.pk) == servable
    assert not coding_views._servable_questions().filter(pk=excluded.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("bad_id", [None, "", "abc", 0, -1, 10_000_001])
def test_a_malformed_or_unknown_id_is_refused_not_a_500(bad_id, servable):
    """
    Every shape of unusable id resolves to "not a servable question". A
    malformed id must not become a 500, and must not fall through to a real
    row either.
    """
    assert coding_views._servable_question(bad_id) is None


@pytest.mark.django_db
def test_a_null_suite_cannot_exist_at_all(topic):
    """
    The brief asked whether a NULL `hidden_test_cases` is refused. It cannot
    occur: the column is NOT NULL, so the selector's `isnull=True` exclusion
    guards a state the schema already forbids. Asserted rather than assumed,
    because "we exclude it" and "it cannot exist" are different claims and
    only one of them survives a schema change.
    """
    from django.db import IntegrityError, transaction

    question = make_question(topic)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Question.objects.filter(pk=question.pk).update(
                hidden_test_cases=None)


# ═════════════════════════════════════════════════════════════
# Submit
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_submit_still_works_for_a_servable_question(api_client, user, servable,
                                                    monkeypatch):
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    api_client.force_authenticate(user=user)

    response = submit(api_client, servable.pk)

    assert response.status_code == 200
    assert sink.get("calls"), "Judge0 was never called for a servable question"
    assert CodeSubmission.objects.filter(question=servable).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("fixture_name", ["placeholder", "empty_suite"])
def test_submit_refuses_an_unservable_question(fixture_name, request,
                                               api_client, user, monkeypatch):
    excluded = request.getfixturevalue(fixture_name)
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    api_client.force_authenticate(user=user)

    response = submit(api_client, excluded.pk)

    assert response.status_code == 409
    assert response.data["detail"] == "question_not_gradable"
    # The refusal happens BEFORE execution, and leaves no trace.
    assert not sink.get("calls"), "Judge0 ran for an unservable question"
    assert not CodeSubmission.objects.filter(question=excluded).exists()


@pytest.mark.django_db
def test_submit_still_404s_for_a_question_that_does_not_exist(api_client, user,
                                                              monkeypatch):
    """
    A nonexistent id and an unservable one are different conditions and keep
    different codes — collapsing them would tell a client the row exists.
    """
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    api_client.force_authenticate(user=user)

    response = submit(api_client, 10_000_001)

    # The serializer rejects an unknown id before the view is reached.
    assert response.status_code in (400, 404)
    assert not sink.get("calls")


@pytest.mark.django_db
def test_no_learner_model_effect_from_a_refused_submission(
        api_client, user, placeholder, monkeypatch):
    """Refusal must not touch the adaptive path at all."""
    from groups.models import UserCodingProfile

    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy({}))
    api_client.force_authenticate(user=user)
    before = UserCodingProfile.objects.filter(user=user).first()
    before_total = before.total_submissions if before else 0

    submit(api_client, placeholder.pk)

    after = UserCodingProfile.objects.filter(user=user).first()
    assert (after.total_submissions if after else 0) == before_total
    assert CodeSubmission.objects.filter(adaptive_eligible=True).count() == 0


# ═════════════════════════════════════════════════════════════
# Run
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_run_still_wraps_a_servable_question(api_client, user, servable,
                                             monkeypatch):
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    api_client.force_authenticate(user=user)

    response = run(api_client, servable.pk)

    assert response.status_code == 200
    executed = sink["calls"][0]["source"]
    assert executed != SOLUTION.strip(), "Run did not build the question's harness"


@pytest.mark.django_db
@pytest.mark.parametrize("fixture_name", ["placeholder", "empty_suite"])
def test_run_does_not_build_an_unservable_questions_harness(
        fixture_name, request, api_client, user, monkeypatch):
    """
    Run is a scratchpad, so it degrades to the existing unknown-id behaviour —
    execute the source as written — rather than inventing a refusal. What it
    must NOT do is build and execute the excluded question's wrapper.
    """
    excluded = request.getfixturevalue(fixture_name)
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    api_client.force_authenticate(user=user)

    response = run(api_client, excluded.pk)

    assert response.status_code == 200
    assert sink["calls"][0]["source"] == SOLUTION.strip()


@pytest.mark.django_db
def test_run_with_an_unknown_id_is_unchanged(api_client, user, monkeypatch):
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    api_client.force_authenticate(user=user)

    response = run(api_client, 10_000_001)

    assert response.status_code == 200
    assert sink["calls"][0]["source"] == SOLUTION.strip()


# ═════════════════════════════════════════════════════════════
# Both endpoints, together
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_neither_endpoint_can_reach_what_the_selector_excludes(
        api_client, user, placeholder, monkeypatch):
    """
    The property in one test: closing one endpoint and not the other leaves
    the bypass open.
    """
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    api_client.force_authenticate(user=user)

    submitted = submit(api_client, placeholder.pk)
    ran = run(api_client, placeholder.pk)

    assert submitted.status_code == 409
    assert ran.status_code == 200
    # Whatever Judge0 saw, it was never the excluded question's harness.
    for call in sink.get("calls", []):
        assert call["source"] == SOLUTION.strip()
    assert not CodeSubmission.objects.filter(question=placeholder).exists()


@pytest.mark.django_db
def test_a_published_verified_question_is_unaffected(api_client, user, topic,
                                                     monkeypatch):
    """
    Mirrors q3309 and q1436: the two production questions that are PUBLISHED
    and ORACLE_VERIFIED must keep working exactly as before.
    """
    question = make_question(
        topic, status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    api_client.force_authenticate(user=user)

    assert coding_views._servable_question(question.pk) == question
    assert submit(api_client, question.pk).status_code == 200
    assert run(api_client, question.pk).status_code == 200
    assert CodeSubmission.objects.filter(question=question).count() == 1


@pytest.mark.django_db
def test_a_draft_unverified_question_is_still_servable_for_now(
        api_client, user, servable, monkeypatch):
    """
    This phase closes the ID BYPASS, not the serving policy. A DRAFT,
    UNVERIFIED question with real content and real tests is still served —
    1,782 of production's 1,784 servable questions are in that state, and
    changing it is the open product decision, not this change.
    """
    assert servable.status == Question.STATUS_DRAFT
    assert servable.trust_state == Question.TRUST_UNVERIFIED

    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy({}))
    api_client.force_authenticate(user=user)

    assert submit(api_client, servable.pk).status_code == 200
