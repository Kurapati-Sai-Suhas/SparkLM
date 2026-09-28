"""
The structural escape quarantine (Phase 1 M17.1).

M17 quarantined a question only when its Python starter DECLARED a structure
its contract cannot build. The binding census found tree and list problems
whose starters declare nothing — an unannotated `root`, or a starter that
does not parse — and all of them were served: `blocker(q617)` was None, and a
correct `mergeTrees` failed.

Held here, behaviourally:

  * each escape shape is detected from the question's own signature and
    starters — never from its title;
  * a detected escapee is refused on EVERY serving path, before Judge0, with
    no submission row, no learner-state change and no adaptive routing;
  * ordinary questions — and the shapes the rule deliberately does not cover —
    stay servable, and "not adaptive-eligible" never becomes "not servable".
"""

import json

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from groups import coding_views, deliverability
from groups.agent import tools
from groups.models import (
    CodeSubmission, CodingPortal, Question, RecommendationLog, Topic,
    UserCodingProfile,
)

# ── the escape shapes the census found (fixture data, not production's) ───

UNANNOTATED_ROOT = (
    "# Definition for a binary tree node.\n"
    "# class TreeNode:\n#     def __init__(self, val=0): ...\n"
    "class Solution:\n"
    "    def countUnivalSubtrees(self, root):\n"
    "        pass\n")
UNANNOTATED_HEAD = ("class Solution:\n"
                    "    def swapPairs(self, head):\n"
                    "        pass\n")
UNANNOTATED_LISTS = ("class Solution:\n"
                     "    def mergeInBetween(self, list1, a: int, b: int, list2):\n"
                     "        pass\n")
UNANNOTATED_TREE_AND_IDS = ("class Solution:\n"
                            "    def findDistance(self, tree, node1: int, node2: int) -> int:\n"
                            "        pass\n")
#: Flattened onto one line, as q617 is stored; it does not parse.
UNPARSEABLE_NAMING = ("class Solution: def mergeTrees(self, root1: Optional[TreeNode], "
                      "root2: Optional[TreeNode]) -> Optional[TreeNode]: pass")
#: Does not parse and names nothing itself — only the C++ signature does (q427).
UNPARSEABLE_PLAIN = "class Solution: def construct(self, grid) -> Node pass"

# ── positive controls ───────────────────────────────────────────────────

ARRAY = ("class Solution:\n"
         "    def total(self, nums: list[int]) -> int:\n"
         "        pass\n")
STRING = ("class Solution:\n"
          "    def reverse(self, s: str) -> str:\n"
          "        pass\n")
SCALAR = ("class Solution:\n"
          "    def square(self, n: int) -> int:\n"
          "        pass\n")
TREE_V2 = ("class Solution:\n"
           "    def maxDepth(self, root: Optional[TreeNode]) -> int:\n"
           "        pass\n")
#: The declaration wins over the name: `root: list` is a list (q230's shape).
ANNOTATED_ROOT_LIST = ("class Solution:\n"
                       "    def kthSmallest(self, root: list, k: int) -> int:\n"
                       "        pass\n")
#: `node` is deliberately not in the convention — in graph problems it is an id.
NODE_ID = ("class Solution:\n"
           "    def degree(self, node, edges: list[list[int]]) -> int:\n"
           "        pass\n")
UNPARSEABLE_NO_STRUCTURE = "class Solution: def f(self, x: int -> int: pass"
#: A design class with no `Solution` (q449's shape): a different defect, and
#: deliberately outside this rule.
DESIGN = ("class Codec:\n"
          "    def serialize(self, root):\n"
          "        pass\n")

CASES = [{"stdin": "1", "expected_output": "1"},
         {"stdin": "2", "expected_output": "2"}]
ECHO = "class Solution:\n    def square(self, n: int) -> int:\n        return n\n"


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Escape Portal")
    made, _ = Topic.objects.get_or_create(
        name="EscapeTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


@pytest.fixture
def learner(db, django_user_model):
    user = django_user_model.objects.create_user(
        username="escape-learner", password="pw", email="esc@example.com")
    UserCodingProfile.objects.create(user=user, elo_rating=1200)
    return user


@pytest.fixture
def client(learner):
    api = APIClient()
    api.force_authenticate(user=learner)
    return api


def make(topic, pk, python=None, *, version="v1", status="DRAFT",
         verified=False, difficulty=1200.0, **starters):
    boilerplate = dict(starters)
    if python is not None:
        boilerplate["python"] = python
    return Question.objects.create(
        id=pk, title=f"Q{pk}", content="A real statement.", topic=topic,
        base_difficulty=difficulty, boilerplate_code=boilerplate,
        hidden_test_cases=json.loads(json.dumps(CASES)), hidden_wrapper_code={},
        execution_contract_version=version, status=status,
        trust_state="ORACLE_VERIFIED" if verified else "UNVERIFIED",
        verified_language="python" if verified else None)


def servable_ids():
    return set(coding_views._servable_questions().values_list("pk", flat=True))


def judge0_spy(sink):
    def runner(source, language, stdin=""):
        sink.setdefault("calls", []).append(source)
        return {"status": "Accepted", "status_id": 3, "stdout": stdin.strip(),
                "stderr": "", "compile_output": "", "time": "0.01",
                "memory": 512}
    return runner


# ═════════════════════════════════════════════════════════════
# Detection — from the signature and starters, never the title
# ═════════════════════════════════════════════════════════════

ESCAPES = [
    ("unannotated root", dict(python=UNANNOTATED_ROOT)),
    ("unannotated head, Java declares ListNode",
     dict(python=UNANNOTATED_HEAD,
          java="class Solution { public ListNode swapPairs(ListNode head) { return head; } }")),
    ("unannotated list1/list2", dict(python=UNANNOTATED_LISTS)),
    ("unannotated tree beside integer ids", dict(python=UNANNOTATED_TREE_AND_IDS)),
    ("unparseable, names TreeNode", dict(python=UNPARSEABLE_NAMING)),
    ("unparseable, only C++ names Node",
     dict(python=UNPARSEABLE_PLAIN, cpp="Node* construct(vector<vector<int>>& grid);")),
    ("unparseable, only JS (stored as `js`) names TreeNode",
     dict(python=UNPARSEABLE_PLAIN.replace("Node", "int"),
          js="/** @param {TreeNode} root */ var f = function(root) {};")),
]


@pytest.mark.django_db
@pytest.mark.parametrize("label,starters", ESCAPES, ids=[e[0] for e in ESCAPES])
def test_every_escape_shape_is_quarantined(topic, label, starters):
    row = make(topic, 9600, **starters)

    verdict = deliverability.blocker(row)

    assert verdict is not None and verdict.cause == deliverability.STRUCTURAL_UNDECLARED
    assert row.pk not in servable_ids()
    assert coding_views._servable_question(row.pk) is None


@pytest.mark.django_db
def test_the_title_is_never_the_evidence(topic):
    """A tree-sounding title on a plain signature is served; a plain title on
    a tree signature is not."""
    decoy = make(topic, 9610, python=SCALAR)
    Question.objects.filter(pk=decoy.pk).update(title="Binary Tree Linked List Root")
    plain_title = make(topic, 9611, python=UNANNOTATED_ROOT)

    # Both entry points: serving's one-query path and the per-question
    # verdict, which the census and every report ask directly.
    assert decoy.pk in servable_ids()
    assert deliverability.blocker(Question.objects.get(pk=decoy.pk)) is None
    assert plain_title.pk not in servable_ids()
    assert deliverability.blocker(plain_title) is not None


@pytest.mark.django_db
def test_annotating_the_parameter_hands_it_back_to_m17s_rule(topic):
    """
    Unlike the declared case, v2 alone does not rescue an escapee — it builds
    from the declaration. Declare the structure and M17's rule takes over:
    quarantined under v1, served under v2.
    """
    row = make(topic, 9612, python=UNANNOTATED_ROOT, version="v2")
    assert row.pk not in servable_ids()

    annotated = UNANNOTATED_ROOT.replace("(self, root)",
                                         "(self, root: Optional[TreeNode])")
    Question.objects.filter(pk=row.pk).update(boilerplate_code={"python": annotated})
    assert row.pk in servable_ids()

    Question.objects.filter(pk=row.pk).update(execution_contract_version="v1")
    assert row.pk not in servable_ids()
    assert deliverability.blocker(Question.objects.get(pk=row.pk)).cause != \
        deliverability.STRUCTURAL_UNDECLARED


@pytest.mark.django_db
def test_detection_still_costs_one_query(topic, django_assert_num_queries):
    make(topic, 9613, python=UNPARSEABLE_PLAIN, cpp="Node* f();")
    make(topic, 9614, python=UNANNOTATED_HEAD)
    make(topic, 9615, python=ARRAY)
    with django_assert_num_queries(1):
        blocked = deliverability.undeliverable_ids(Question.objects.all())
    assert set(blocked) == {9613, 9614}


# ═════════════════════════════════════════════════════════════
# Every serving path refuses an escapee
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_recommender_and_traffic_cop_never_serve_it(client, learner, topic):
    """It sits at the learner's exact rating; the control is 300 Elo away."""
    escapee = make(topic, 9620, python=UNANNOTATED_ROOT, difficulty=1200.0)
    control = make(topic, 9621, python=SCALAR, difficulty=1500.0)

    assert coding_views._select_question(learner, topic.name, 1200.0) == control
    response = client.get(reverse("code-next-problem"), {"topic": topic.name})
    assert response.status_code == 200
    assert int(response.data["id"]) == control.pk
    assert not RecommendationLog.objects.filter(problem_id=str(escapee.pk)).exists()


@pytest.mark.django_db
def test_it_never_enters_adaptive_routing_even_alone(client, learner, topic):
    escapee = make(topic, 9622, python=UNPARSEABLE_NAMING)

    response = client.get(reverse("code-next-problem"), {"topic": topic.name})

    assert response.data.get("next_problem") is None and "id" not in response.data
    assert not RecommendationLog.objects.filter(problem_id=str(escapee.pk)).exists()
    assert topic.name not in coding_views._topics_with_candidates(learner)


@pytest.mark.django_db
def test_submit_refuses_before_judge0_and_writes_nothing(client, learner, topic,
                                                         monkeypatch):
    escapee = make(topic, 9623, python=UNANNOTATED_HEAD)
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))
    profile = UserCodingProfile.objects.get(user=learner)

    response = client.post(reverse("code-submit"), {
        "problem_id": escapee.pk, "code": ECHO, "language": "python",
    }, format="json")

    assert response.status_code == 409
    assert response.data["detail"] == "question_not_gradable"
    assert "calls" not in sink
    assert not CodeSubmission.objects.filter(question=escapee).exists()
    profile.refresh_from_db()
    assert profile.elo_rating == 1200


@pytest.mark.django_db
def test_run_does_not_execute_its_harness(client, topic, monkeypatch):
    escapee = make(topic, 9624, python=UNANNOTATED_ROOT)
    sink = {}
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy(sink))

    response = client.post(reverse("code-run"), {
        "problem_id": escapee.pk, "code": ECHO, "language": "python",
        "stdin": "[1,null,2]"}, format="json")

    assert response.status_code == 200
    assert sink["calls"] == [ECHO.strip()]


@pytest.mark.django_db
def test_the_agent_neither_offers_nor_endorses_it(learner, topic):
    """A trusted escapee: the offer list and the answer check both refuse it."""
    trusted_escapee = make(topic, 9625, python=UNANNOTATED_ROOT,
                           status="PUBLISHED", verified=True)
    trusted_plain = make(topic, 9626, python=SCALAR, status="PUBLISHED",
                         verified=True)
    session = tools.Session(user=learner)

    offered = tools.get_candidate_problems(session)["candidates"]

    assert [c["question_id"] for c in offered] == [trusted_plain.pk]
    session.offered_question_ids.add(trusted_escapee.pk)
    with pytest.raises(tools.RecommendationRejected, match="not servable"):
        tools.validate_recommendation(session, trusted_escapee.pk)


@pytest.mark.django_db
def test_trust_summary_reports_it_unservable_without_touching_trust(topic):
    trusted_escapee = make(topic, 9627, python=UNANNOTATED_ROOT,
                           status="PUBLISHED", verified=True)
    summary = trusted_escapee.trust_summary()
    assert summary["servable"] is False
    assert summary["adaptive_eligible"] is True        # eligibility unchanged


# ═════════════════════════════════════════════════════════════
# Positive controls
# ═════════════════════════════════════════════════════════════

CONTROLS = [
    ("array", ARRAY, "v1"),
    ("string", STRING, "v1"),
    ("scalar", SCALAR, "v1"),
    ("structural v2", TREE_V2, "v2"),
    ("annotated root: list", ANNOTATED_ROOT_LIST, "v1"),
    ("unannotated node id", NODE_ID, "v1"),
    ("unparseable, no structure", UNPARSEABLE_NO_STRUCTURE, "v1"),
    ("design class, no Solution", DESIGN, "v1"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("label,python,version", CONTROLS,
                         ids=[c[0] for c in CONTROLS])
def test_ordinary_questions_stay_servable(topic, label, python, version):
    row = make(topic, 9630, python=python, version=version)
    assert deliverability.blocker(row) is None
    assert row.pk in servable_ids()


@pytest.mark.django_db
def test_practice_draft_stays_servable_and_stays_non_adaptive(topic, client,
                                                              learner,
                                                              monkeypatch):
    practice = make(topic, 9631, python=SCALAR)
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_spy({}))

    assert practice.is_adaptive_eligible is False
    assert practice.pk in servable_ids()
    response = client.post(reverse("code-submit"), {
        "problem_id": practice.pk, "code": ECHO, "language": "python",
    }, format="json")
    assert response.status_code == 200
    assert CodeSubmission.objects.get(question=practice).adaptive_eligible is False
