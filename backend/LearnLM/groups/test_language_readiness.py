"""
Language-readiness gate (M2 P2.35 / Phase 1 M1).

The invariant: the platform can say, per question and per language, whether
the starter it HANDS OUT can execute — and it says so where the language is
chosen, with the reason attached.

The defect this addresses, measured in the P2.34 audit: all 638 shipped C++
starters lack `main()`, C++ is self-contained so the source reaches Judge0
unwrapped, and `adaptive_eligible` carries no language term — so a link
failure counted as a genuine attempt against a "verified" question.

── Where the gate is NOT ───────────────────────────────────────────────────

Not at submission. The first version of this milestone refused a submission
whose language failed the check, which judged the learner by the starter
rather than by their own code: a C++ learner who writes a complete program
with `main` is entitled to be graded regardless of what the starter contains.
28 existing tests said so immediately, and
`test_a_submission_is_NOT_refused_for_an_unready_starter` keeps it that way.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from common import languages
from groups import language_readiness as lr
from groups import trust_coverage
from groups.models import CodingPortal, Question, Topic

User = get_user_model()

PY_OK = "class Solution:\n    def reverse(self, x: int) -> int:\n        pass\n"
PY_UNDEFINED = ("class Solution:\n"
                "    def f(self, nums: List[int]) -> int:\n        pass\n")
PY_STRUCTURAL = ("class Solution:\n"
                 "    def f(self, root: TreeNode) -> bool:\n        pass\n")
JAVA_OK = ("class Solution {\n    public int reverse(int x) {\n"
           "        return 0;\n    }\n}\n")
JS_OK = "class Solution {\n    reverse(x) {\n        return 0;\n    }\n}\n"
JS_PROTOTYPE = ("var Solution = function() {};\n"
                "Solution.prototype.reverse = function(x) { return 0; };\n")
CPP_NO_MAIN = ("class Solution {\npublic:\n    void reverse() {\n    }\n};\n")
CPP_WITH_MAIN = ("#include <iostream>\nint main() {\n"
                 "    int x; std::cin >> x; std::cout << x;\n    return 0;\n}\n")


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Readiness Portal")
    made, _ = Topic.objects.get_or_create(
        name="ReadinessTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, boilerplate, *, contract="v1"):
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}", content="Statement.",
        topic=topic, base_difficulty=1300.0,
        boilerplate_code=boilerplate,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version=contract)


# ═════════════════════════════════════════════════════════════
# The C++ defect this milestone exists for
# ═════════════════════════════════════════════════════════════

def test_a_cpp_starter_without_main_is_not_ready():
    result = lr.assess_source(CPP_NO_MAIN, "cpp")

    assert result.verdict == lr.NOT_READY
    assert not result.ready
    assert "main()" in result.reason


def test_a_cpp_starter_with_main_is_ready():
    assert lr.assess_source(CPP_WITH_MAIN, "cpp").verdict == lr.READY


def test_c_is_judged_by_the_same_self_contained_rule():
    """C shares the model; it must not be silently exempt."""
    assert lr.assess_source(CPP_NO_MAIN, "c").verdict == lr.NOT_READY
    assert lr.assess_source(CPP_WITH_MAIN, "c").verdict == lr.READY


def test_the_self_contained_set_is_read_from_the_registry():
    """
    Not a local list. If a language's execution model changes in
    `common.languages`, readiness must follow rather than keep its own copy.
    """
    assert languages.get("cpp").self_contained is True
    assert languages.get("c").self_contained is True
    assert languages.get("python").self_contained is False


# ═════════════════════════════════════════════════════════════
# Reflection languages
# ═════════════════════════════════════════════════════════════

def test_a_clean_python_starter_is_ready():
    assert lr.assess_source(PY_OK, "python").verdict == lr.READY


def test_an_undefined_annotation_name_is_not_ready():
    result = lr.assess_source(PY_UNDEFINED, "python")

    assert result.verdict == lr.NOT_READY
    assert "List" in result.reason and "NameError" in result.reason


def test_a_structural_type_is_not_ready_in_python():
    result = lr.assess_source(PY_STRUCTURAL, "python")

    assert result.verdict == lr.NOT_READY
    assert "TreeNode" in result.reason


def test_java_with_a_solution_class_is_unknown_not_ready_or_broken():
    """
    Honest verdict: the shape is right and no compiler is available here, so
    the checker declines to claim more than it can prove.
    """
    result = lr.assess_source(JAVA_OK, "java")

    assert result.verdict == lr.UNKNOWN
    assert result.ready is True          # UNKNOWN is servable


def test_javascript_prototype_form_is_accepted():
    """
    The JS harness walks the prototype chain, so `Solution.prototype.f` is a
    valid definition. An earlier check keyed on `class Solution` and wrongly
    condemned 422 starters that execute perfectly.
    """
    assert lr.assess_source(JS_PROTOTYPE, "javascript").ready is True
    assert lr.assess_source(JS_OK, "javascript").ready is True


def test_a_starter_without_solution_is_not_ready():
    result = lr.assess_source("function foo() { return 1; }", "javascript")

    assert result.verdict == lr.NOT_READY
    assert "Solution" in result.reason


def test_an_empty_or_missing_starter_is_not_ready():
    for source in ("", "   ", None):
        assert lr.assess_source(source, "python").verdict == lr.NOT_READY


def test_an_unregistered_language_is_not_ready():
    result = lr.assess_source(PY_OK, "rust")

    assert result.verdict == lr.NOT_READY
    assert "not a registered language" in result.reason


# ═════════════════════════════════════════════════════════════
# Question-level assessment
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_ready_languages_reflects_the_stored_starters(topic):
    question = make_question(topic, 9500, {
        "python": PY_OK, "java": JAVA_OK, "cpp": CPP_NO_MAIN})

    ready = lr.ready_languages(question)

    assert "python" in ready and "java" in ready
    assert "cpp" not in ready            # no main()
    assert "javascript" not in ready     # no starter at all


@pytest.mark.django_db
def test_blocked_languages_explains_each_refusal(topic):
    question = make_question(topic, 9501, {"python": PY_OK})

    blocked = lr.blocked_languages(question)

    assert set(blocked) == {"java", "cpp", "c", "javascript"}
    for reason in blocked.values():
        assert reason


@pytest.mark.django_db
def test_javascript_is_found_under_either_spelling(topic):
    """
    Seed generations filed JS under both keys. A single-key lookup reported a
    present language as absent — the bug `wrapper_spellings` exists to stop.
    """
    under_js = make_question(topic, 9502, {"python": PY_OK, "js": JS_OK})
    under_full = make_question(topic, 9503,
                               {"python": PY_OK, "javascript": JS_OK})

    assert lr.assess(under_js, "javascript").ready is True
    assert lr.assess(under_full, "javascript").ready is True


@pytest.mark.django_db
def test_a_v2_question_requires_a_v2_harness(topic):
    """C++ has no v2 harness and no main; both reasons are disqualifying."""
    question = make_question(topic, 9504, {"cpp": CPP_NO_MAIN}, contract="v2")

    assert lr.assess(question, "cpp").verdict == lr.NOT_READY


# ═════════════════════════════════════════════════════════════
# Where the gate belongs — and where it does NOT
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def learner(db):
    return User.objects.create_user(username="lr-learner", password="pw",
                                    email="lr@example.com")


@pytest.mark.django_db
def test_readiness_is_reported_by_the_serving_path(learner, topic):
    """
    The client needs to know which languages work BEFORE the learner picks
    one. Reported additively, next to `trust`, so no existing key moves.
    """
    question = make_question(topic, 9510, {
        "python": PY_OK, "cpp": CPP_NO_MAIN})

    payload = {
        "trust": question.trust_summary(),
        "languages": {
            "ready": lr.ready_languages(question),
            "blocked": lr.blocked_languages(question),
        },
    }

    assert "python" in payload["languages"]["ready"]
    assert "cpp" not in payload["languages"]["ready"]
    assert "main()" in payload["languages"]["blocked"]["cpp"]


@pytest.mark.django_db
def test_a_submission_is_NOT_refused_for_an_unready_starter(learner, topic):
    """
    Readiness describes the code the platform HANDS OUT. What runs is what the
    learner wrote, so a C++ learner who supplies a complete program with
    `main` must still be graded even though the shipped starter has none.

    The first version of this milestone refused here and broke 28 tests whose
    questions ship no boilerplate at all. Pinned so the gate is not
    reintroduced at this boundary.
    """
    from unittest.mock import patch

    question = make_question(topic, 9511, {
        "python": PY_OK, "cpp": CPP_NO_MAIN})
    client = APIClient()
    client.force_authenticate(user=learner)

    with patch("groups.coding_views.GradingService.grade") as grade:
        grade.return_value = type("G", (), {
            "final_status": "wrong_answer", "results": [],
            "stored_code": CPP_WITH_MAIN, "passed": 0, "total": 1,
            "all_passed": False})()
        response = client.post("/api/code/submit/", {
            "problem_id": question.pk, "code": CPP_WITH_MAIN,
            "language": "cpp",
        }, format="json")

    assert response.status_code != 409, response.data


@pytest.mark.django_db
def test_an_unregistered_language_is_still_rejected_by_the_serializer(
        learner, topic):
    """The pre-existing gate stays; this milestone adds to it, not replaces."""
    question = make_question(topic, 9512, {"python": PY_OK})
    client = APIClient()
    client.force_authenticate(user=learner)

    response = client.post("/api/code/submit/", {
        "problem_id": question.pk, "code": "x", "language": "rust",
    }, format="json")

    assert response.status_code == 400


# ═════════════════════════════════════════════════════════════
# One definition, not two
# ═════════════════════════════════════════════════════════════

def test_trust_coverage_delegates_rather_than_reimplementing():
    """
    `trust_coverage.harness_blocker` owned the first copy of this rule. It
    must now defer, or the worklist and the serving gate can disagree about
    whether the same starter runs.
    """
    import inspect

    source = inspect.getsource(trust_coverage)

    assert "language_readiness" in source
    assert "ast.parse" not in source          # the rule no longer lives here


@pytest.mark.parametrize("starter,language", [
    (PY_OK, "python"), (PY_UNDEFINED, "python"), (PY_STRUCTURAL, "python"),
    (CPP_NO_MAIN, "cpp"), (JS_PROTOTYPE, "javascript"),
])
def test_the_two_entry_points_agree(starter, language):
    blocker = trust_coverage.harness_blocker(starter, language)
    readiness = lr.assess_source(starter, language)

    assert (blocker is None) == readiness.ready
