"""
The deliverability quarantine (Phase 1 M17).

A question whose signature declares a structure its contract does not build
is excluded from every serving path. Under v1 the harness hands `isSameTree`
the string "[1,2,3]" where it expects a node, so a correct solution is graded
Wrong Answer — the M14 multilingual audit executed exactly that for q100.

The properties held here, all behavioural:

  * a structurally undeliverable question is refused on EVERY path — the
    recommender, Traffic Cop's endpoint, Run, Submit, the agent;
  * the rule is about deliverability and nothing else: a deliverable DRAFT
    question is still practice, a PUBLISHED one is still served, and adaptive
    eligibility is exactly what the model says it is;
  * the SQL prefilter plus the AST verdict equals the readiness classifier on
    every declaration form, in one query, and never serves a stale answer.
"""

import json

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from groups import coding_views, content_quarantine, deliverability
from groups import language_readiness
from groups.agent import tools
from groups.models import (
    CodeSubmission, CodingPortal, Question, Topic, UserCodingProfile,
)

#: q100's shape: v1, two tree parameters, a scalar answer.
TREE_V1 = ("class Solution:\n"
           "    def isSameTree(self, p: TreeNode | None, "
           "q: TreeNode | None) -> bool:\n"
           "        pass\n")
PLAIN = ("class Solution:\n"
         "    def solve(self, n: int) -> int:\n"
         "        pass\n")
CASES = [{"stdin": "1", "expected_output": "1"},
         {"stdin": "2", "expected_output": "2"}]
SOLUTION = "class Solution:\n    def solve(self, n: int) -> int:\n        return n\n"


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Deliverability Portal")
    row, _ = Topic.objects.get_or_create(
        name="DeliverabilityTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return row


@pytest.fixture
def learner(db, django_user_model):
    user = django_user_model.objects.create_user(
        username="deliverability-learner", password="pw",
        email="d@example.com")
    UserCodingProfile.objects.create(user=user, elo_rating=1200)
    return user


@pytest.fixture
def client(learner):
    api = APIClient()
    api.force_authenticate(user=learner)
    return api


def make_question(topic, pk, *, starter=PLAIN, version="v1", status="DRAFT",
                  verified=False, difficulty=1200.0, **overrides):
    fields = dict(
        id=pk, title=f"Q{pk}", content="A real statement.", topic=topic,
        base_difficulty=difficulty,
        boilerplate_code={"python": starter},
        hidden_test_cases=json.loads(json.dumps(CASES)),
        hidden_wrapper_code={}, execution_contract_version=version,
        status=status,
        trust_state=("ORACLE_VERIFIED" if verified else "UNVERIFIED"),
        verified_language=("python" if verified else None))
    fields.update(overrides)
    return Question.objects.create(**fields)


def servable_ids():
    return set(coding_views._servable_questions().values_list("pk", flat=True))


def judge0_spy(sink):
    """Records every call and echoes stdin, which is what SOLUTION returns."""
    def runner(source, language, stdin=""):
        sink.setdefault("calls", []).append(
            {"source": source, "language": language, "stdin": stdin})
        return {"status": "Accepted", "status_id": 3, "stdout": stdin.strip(),
                "stderr": "", "compile_output": "", "time": "0.01",
                "memory": 512}
    return runner


# ═════════════════════════════════════════════════════════════
# 1-2. The undeliverable DRAFT question is refused everywhere
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_an_undeliverable_draft_question_is_not_servable(topic):
    """q100 exactly: v1, DRAFT, UNVERIFIED, a TreeNode signature."""
    q100 = make_question(topic, 9100, starter=TREE_V1)

    assert q100.status == "DRAFT" and q100.trust_state == "UNVERIFIED"
    assert q100.pk not in servable_ids()
    assert coding_views._servable_question(q100.pk) is None


@pytest.mark.django_db
def test_the_recommender_never_selects_it_even_when_it_is_the_best_match(
        topic, learner):
    """
    The undeliverable question sits EXACTLY at the learner's rating; the
    deliverable one is 250 Elo away. Ordering alone would pick the first.
    """
    make_question(topic, 9101, starter=TREE_V1, difficulty=1200.0)
    fallback = make_question(topic, 9102, difficulty=1450.0)

    chosen = coding_views._select_question(learner, topic.name, 1200.0)

    assert chosen == fallback
    assert set(coding_views._candidate_questions(learner)
               .values_list("pk", flat=True)) == {fallback.pk}


@pytest.mark.django_db
def test_traffic_cop_serves_only_a_deliverable_candidate(client, topic,
                                                         monkeypatch):
    """Through the real endpoint, whichever route the classifier picks."""
    make_question(topic, 9103, starter=TREE_V1)
    deliverable = make_question(topic, 9104, difficulty=1500.0)

    response = client.get(reverse("code-next-problem"), {"topic": topic.name})

    assert response.status_code == 200
    assert int(response.data["id"]) == deliverable.pk


@pytest.mark.django_db
def test_a_topic_holding_only_undeliverable_questions_serves_nothing(
        client, learner, topic):
    make_question(topic, 9105, starter=TREE_V1)

    response = client.get(reverse("code-next-problem"), {"topic": topic.name})

    assert response.status_code == 200
    assert response.data.get("next_problem") is None
    assert "id" not in response.data
    assert topic.name not in coding_views._topics_with_candidates(learner)


@pytest.mark.django_db
def test_submit_refuses_it_before_grading(client, learner, topic,
                                          monkeypatch):
    """409 `question_not_gradable`, no Judge0 call, no row, no rating move."""
    q100 = make_question(topic, 9106, starter=TREE_V1)
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    elo_before = UserCodingProfile.objects.get(user=learner).elo_rating

    response = client.post(reverse("code-submit"), {
        "problem_id": q100.pk, "code": SOLUTION, "language": "python",
    }, format="json")

    assert response.status_code == 409
    assert response.data["detail"] == "question_not_gradable"
    assert "calls" not in sink
    assert not CodeSubmission.objects.filter(question=q100).exists()
    assert UserCodingProfile.objects.get(user=learner).elo_rating == elo_before


@pytest.mark.django_db
def test_run_never_executes_its_harness(client, topic, monkeypatch):
    """
    Run keeps its documented behaviour for an unservable id: the source runs
    as written. What must not happen is the question's harness wrapping it.
    """
    q100 = make_question(topic, 9107, starter=TREE_V1)
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))

    response = client.post(reverse("code-run"), {
        "problem_id": q100.pk, "code": SOLUTION, "language": "python",
        "stdin": "[1]\n[1]",
    }, format="json")

    assert response.status_code == 200
    assert sink["calls"][0]["source"] == SOLUTION.strip()   # Run strips input


@pytest.mark.django_db
def test_the_agent_neither_offers_nor_endorses_it(learner, topic):
    """
    A TRUSTED row that is undeliverable — impossible to verify today, since
    no oracle can pass a v1 tree question, but the two facts are independent
    and each guard must hold on its own.
    """
    trusted_tree = make_question(topic, 9108, starter=TREE_V1,
                                 status="PUBLISHED", verified=True)
    trusted_plain = make_question(topic, 9109, status="PUBLISHED",
                                  verified=True)
    session = tools.Session(user=learner)

    offered = tools.get_candidate_problems(session)

    assert [c["question_id"] for c in offered["candidates"]] == [trusted_plain.pk]
    session.offered_question_ids.add(trusted_tree.pk)   # as if offered earlier
    with pytest.raises(tools.RecommendationRejected, match="not servable"):
        tools.validate_recommendation(session, trusted_tree.pk)
    assert tools.validate_recommendation(
        session, trusted_plain.pk)["question_id"] == trusted_plain.pk


@pytest.mark.django_db
def test_trust_summary_reports_it_as_not_servable(topic):
    q100 = make_question(topic, 9110, starter=TREE_V1)
    assert q100.trust_summary()["servable"] is False


# ═════════════════════════════════════════════════════════════
# 3-5. Deliverability only — not status, not trust
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("status,verified", [
    ("DRAFT", False),
    ("PENDING_REVIEW", False),
    ("PUBLISHED", False),
    ("PUBLISHED", True),
])
def test_servability_is_decided_by_deliverability_alone(topic, status,
                                                        verified):
    """
    For every lifecycle/trust pairing the schema allows, a deliverable
    question is served and an undeliverable one is not. Status and trust do
    not enter the rule in either direction.
    """
    deliverable = make_question(topic, 9120, status=status, verified=verified)
    undeliverable = make_question(topic, 9121, status=status,
                                  verified=verified, starter=TREE_V1)

    assert deliverable.pk in servable_ids()
    assert undeliverable.pk not in servable_ids()


@pytest.mark.django_db
def test_a_deliverable_draft_stays_servable_as_practice(topic, client,
                                                        learner, monkeypatch):
    """
    Not adaptive-eligible does not mean unservable (M2 P2.7c): a DRAFT
    question is practice. It is graded and recorded, and — because it is not
    eligible — it teaches the learner model nothing.
    """
    practice = make_question(topic, 9122)
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy({}))
    elo_before = UserCodingProfile.objects.get(user=learner).elo_rating

    assert practice.is_adaptive_eligible is False
    assert practice.pk in servable_ids()

    response = client.post(reverse("code-submit"), {
        "problem_id": practice.pk, "code": SOLUTION, "language": "python",
    }, format="json")

    assert response.status_code == 200
    row = CodeSubmission.objects.get(question=practice)
    assert row.adaptive_eligible is False
    assert UserCodingProfile.objects.get(user=learner).elo_rating == elo_before


@pytest.mark.django_db
def test_adaptive_eligibility_is_untouched_by_the_quarantine(topic):
    """
    The quarantine decides what is SERVED. It does not read, write or
    re-derive eligibility: an undeliverable row keeps exactly the eligibility
    its status and trust give it, in Python and in SQL.
    """
    trusted_tree = make_question(topic, 9123, starter=TREE_V1,
                                 status="PUBLISHED", verified=True)
    draft_plain = make_question(topic, 9124)

    assert trusted_tree.is_adaptive_eligible is True
    assert trusted_tree.adaptive_eligible_for("python") is True
    assert Question.objects.filter(Question.adaptive_eligible_q(),
                                   pk=trusted_tree.pk).exists()
    assert draft_plain.is_adaptive_eligible is False
    assert trusted_tree.pk not in servable_ids()
    trusted_tree.refresh_from_db()
    assert (trusted_tree.status, trusted_tree.trust_state) == (
        "PUBLISHED", "ORACLE_VERIFIED")


# ═════════════════════════════════════════════════════════════
# 6. q98
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_q98_stays_quarantined_and_is_not_served(topic):
    """
    q98's content hold is untouched — it still blocks approval — and a row
    shaped like it (v1, `Optional[TreeNode]`) is now also out of serving.
    """
    q98 = make_question(
        topic, 98,
        starter=("class Solution:\n"
                 "    def isValidBST(self, root: Optional[TreeNode]) -> bool:\n"
                 "        pass\n"))

    assert content_quarantine.is_quarantined(98)
    assert content_quarantine.blocker_for(98)
    assert q98.pk not in servable_ids()


# ═════════════════════════════════════════════════════════════
# The rule itself
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_migrated_question_returns_on_its_own(topic):
    """
    v2 builds TreeNode, so the same starter is deliverable the moment the
    contract column moves. `Node` has no adapter under any contract.
    """
    tree = make_question(topic, 9130, starter=TREE_V1)
    node = make_question(
        topic, 9131, version="v2",
        starter="class Solution:\n    def f(self, root: 'Node') -> int:\n"
                "        pass\n")
    assert tree.pk not in servable_ids()

    Question.objects.filter(pk=tree.pk).update(execution_contract_version="v2")

    assert tree.pk in servable_ids()
    assert node.pk not in servable_ids()


#: (starter, contract, excluded?) — every declaration form the bank uses,
#: plus the ones the rule deliberately does not act on.
FORMS = [
    (TREE_V1, "v1", True),
    ("class Solution:\n    def f(self, root: Optional[TreeNode]) -> int: pass\n",
     "v1", True),
    ("class Solution:\n    def f(self, root: 'TreeNode') -> int: pass\n",
     "v1", True),
    ("class Solution:\n    def f(self, head: ListNode) -> int: pass\n",
     "", True),                                     # blank contract means v1
    ("class Solution:\n    def f(self, n: int) -> Optional[ListNode]: pass\n",
     "v3", True),
    ("class Solution:\n    def f(self, root: 'Node') -> int: pass\n",
     "v1", True),
    ("class Solution:\n    def f(self, h: ImmutableListNode) -> None: pass\n",
     "v2", True),
    ("class TreeNode:\n    def __init__(self, val: int = 0):\n        pass\n"
     "class Solution:\n    def f(self, root: TreeNode) -> int: pass\n",
     "v1", True),
    ("class Solution:\n    def f(self, root: TreeNode) -> int: pass\n",
     "v9", True),                                   # unknown contract builds nothing
    (TREE_V1, "v2", False),
    (PLAIN, "v1", False),
    (PLAIN, "v9", False),                            # left to the grader, as before
    # Mentioned, never declared: the rule reads declarations.
    ("# TreeNode is defined for you\nclass Solution:\n"
     "    def f(self, root) -> int: pass\n", "v1", False),
    # Starter defects the learner REPLACES; not proof the question cannot pass.
    ("class Solution:\n    def f(self, root: TreeNode -> int: pass\n",
     "v1", False),                                  # unparseable
    ("def f(root: TreeNode) -> int: pass\n", "v1", False),  # no Solution class
    ("class Solution:\n    def f(self, x: Foo) -> int: pass\n", "v1", False),
]


@pytest.mark.django_db
def test_the_prefilter_and_the_classifier_agree_on_every_form(topic):
    """
    The SQL narrows, the readiness classifier decides, and together they must
    equal the classifier applied to every row — the definition the user chose.
    """
    rows = [make_question(topic, 9200 + i, starter=starter, version=version)
            for i, (starter, version, _excluded) in enumerate(FORMS)]
    expected = {row.pk for row, (_s, _v, excluded) in zip(rows, FORMS)
                if excluded}

    by_classifier = {
        row.pk for row in rows
        if language_readiness.assess_source(
            row.boilerplate_code["python"], "python",
            row.execution_contract_version or "v1").cause
        in deliverability.BLOCKING_CAUSES}

    assert set(deliverability.undeliverable_ids(Question.objects.all())) \
        == expected == by_classifier
    assert servable_ids() == {row.pk for row in rows} - expected


@pytest.mark.django_db
def test_a_missing_python_starter_is_not_quarantined(topic):
    """No declaration, no proof. Other languages' starters are not read."""
    row = make_question(topic, 9300, boilerplate_code={
        "javascript": "class Solution { f(root /* TreeNode */) {} }"})
    assert row.pk in servable_ids()


@pytest.mark.django_db
def test_it_costs_one_query(topic, django_assert_num_queries):
    make_question(topic, 9301, starter=TREE_V1)
    make_question(topic, 9302)
    with django_assert_num_queries(1):
        deliverability.undeliverable_ids(Question.objects.all())


@pytest.mark.django_db
def test_nothing_is_cached_across_requests(topic):
    """
    Only the pure (source, contract) verdict is memoised. Rows are read live,
    so an edit to a starter or a contract is visible on the very next call —
    in both directions.
    """
    row = make_question(topic, 9303)
    assert row.pk in servable_ids()

    Question.objects.filter(pk=row.pk).update(
        boilerplate_code={"python": TREE_V1})
    assert row.pk not in servable_ids()

    Question.objects.filter(pk=row.pk).update(execution_contract_version="v2")
    assert row.pk in servable_ids()


@pytest.mark.django_db
def test_an_unknown_contract_does_not_break_serving(topic):
    """One bad row must not turn every learner's request into a 500."""
    bad = make_question(topic, 9304, version="v9")
    assert bad.pk in servable_ids()
