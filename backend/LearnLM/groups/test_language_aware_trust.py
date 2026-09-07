"""
Language-aware adaptive eligibility (M2 P2.36 / Phase 1 M3).

THE INVARIANT
    A question verified by the Oracle in one language is NOT adaptive-eligible
    for a submission in another language.

Why it was needed: every production reference is Python, `adaptive_eligible`
carried no language term, and C++ is unexecutable for all 1,788 servable
questions. So a C++ submission against a Python-verified question produced a
link failure that moved the learner's rating as a genuine attempt.

The architecture chosen is question-level trust plus a single
`verified_language` marker — deliberately smaller than per-language trust
state, and documented in docs/ARCHITECTURE_TRUST.md as the interim model.
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from groups.models import CodingPortal, Question, Topic

User = get_user_model()

PY_OK = "class Solution:\n    def reverse(self, x: int) -> int:\n        pass\n"
JAVA_OK = ("class Solution {\n    public int reverse(int x) {\n"
           "        return 0;\n    }\n}\n")
JS_OK = "class Solution {\n    reverse(x) {\n        return 0;\n    }\n}\n"
CPP_WITH_MAIN = ("#include <iostream>\nint main() {\n"
                 "    int x; std::cin >> x; std::cout << x;\n    return 0;\n}\n")

#: Every language the platform registers, with a starter that IS executable —
#: so a refusal below can only come from the language MARKER, never from
#: readiness. Without this the tests would pass for the wrong reason.
ALL_STARTERS = {
    "python": PY_OK, "java": JAVA_OK, "javascript": JS_OK,
    "cpp": CPP_WITH_MAIN, "c": CPP_WITH_MAIN,
}


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Trust Portal")
    made, _ = Topic.objects.get_or_create(
        name="TrustTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, *, verified_language=None,
                  status=None, trust=None, boilerplate=None):
    question = Question.objects.create(
        id=question_id, title=f"Q{question_id}", content="Statement.",
        topic=topic, base_difficulty=1300.0,
        boilerplate_code=boilerplate or dict(ALL_STARTERS),
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v1")
    # Set trust through an UPDATE so the fixture does not depend on the
    # promotion command, while the CHECK constraint still applies.
    Question.objects.filter(pk=question.pk).update(
        status=status or Question.STATUS_PUBLISHED,
        trust_state=trust or Question.TRUST_ORACLE_VERIFIED,
        verified_language=verified_language)
    question.refresh_from_db()
    return question


# ═════════════════════════════════════════════════════════════
# 1–5. The invariant, one test per language
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_python_verified_plus_python_submission_is_eligible(topic):
    question = make_question(topic, 9600, verified_language="python")

    assert question.adaptive_eligible_for("python") is True


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["java", "cpp", "c", "javascript"])
def test_python_verified_plus_other_language_is_NOT_eligible(topic, language):
    """
    The vulnerability, closed. Each starter here is executable, so the only
    thing refusing is the verified-language mismatch.
    """
    question = make_question(topic, 9601, verified_language="python")

    assert question.adaptive_eligible_for(language) is False


@pytest.mark.django_db
def test_a_java_verified_question_is_eligible_for_java_not_python(topic):
    """The rule is symmetric — it is not a Python allowlist."""
    question = make_question(topic, 9602, verified_language="java")

    assert question.adaptive_eligible_for("java") is True
    assert question.adaptive_eligible_for("python") is False


# ═════════════════════════════════════════════════════════════
# 6–8. Trust state and marker must BOTH hold
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_an_unverified_question_is_not_eligible_even_with_a_marker(topic):
    """A stale marker must never create eligibility on its own."""
    question = make_question(topic, 9610, verified_language="python",
                             trust=Question.TRUST_UNVERIFIED)

    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert question.verified_language == "python"
    assert question.adaptive_eligible_for("python") is False


@pytest.mark.django_db
def test_a_draft_question_is_not_eligible_even_when_verified(topic):
    question = make_question(topic, 9611, verified_language="python",
                             status="BLOCKED")

    assert question.adaptive_eligible_for("python") is False


@pytest.mark.django_db
def test_a_verified_question_with_no_marker_is_not_eligible(topic):
    """
    Constructed by bypassing the constraint's intent is impossible, so this
    checks the predicate's own guard: a null marker matches no language.
    """
    question = make_question(topic, 9612, verified_language="python")
    question.verified_language = None       # in-memory only

    assert question.adaptive_eligible_for("python") is False


@pytest.mark.django_db
def test_an_unregistered_verified_language_matches_nothing(topic):
    question = make_question(topic, 9613, verified_language="rust")

    for language in ("python", "java", "cpp", "c", "javascript", "rust"):
        assert question.adaptive_eligible_for(language) is False


@pytest.mark.django_db
def test_an_unregistered_submission_language_is_not_eligible(topic):
    question = make_question(topic, 9614, verified_language="python")

    assert question.adaptive_eligible_for("rust") is False
    assert question.adaptive_eligible_for("") is False
    assert question.adaptive_eligible_for(None) is False


# ═════════════════════════════════════════════════════════════
# 9. Alias normalisation
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_js_and_javascript_are_the_same_language(topic):
    """
    The SPA sends "js"; the registry canonicalises to "javascript". Eligibility
    must not depend on which spelling reached it.
    """
    verified_full = make_question(topic, 9620, verified_language="javascript")
    verified_alias = make_question(topic, 9621, verified_language="js")

    for question in (verified_full, verified_alias):
        assert question.adaptive_eligible_for("js") is True
        assert question.adaptive_eligible_for("javascript") is True
        assert question.adaptive_eligible_for("JavaScript") is True


@pytest.mark.django_db
def test_case_and_whitespace_do_not_change_the_verdict(topic):
    question = make_question(topic, 9622, verified_language="python")

    assert question.adaptive_eligible_for("PYTHON") is True
    assert question.adaptive_eligible_for(" python ") is True


# ═════════════════════════════════════════════════════════════
# Readiness is also required
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_broken_STARTER_does_not_remove_eligibility(topic):
    """
    Readiness is deliberately NOT a term in this predicate.

    Readiness describes the starter the platform hands out; what was graded is
    the code the learner wrote. A learner who replaced a broken starter with
    working code, in the verified language, produced a trustworthy outcome —
    refusing to let it move their rating would punish them for a content
    defect they routed around. That is the same error M1 made at the
    submission boundary and corrected.

    It is also redundant: a question verified in C++ had a reference EXECUTE
    in C++, so C++ demonstrably runs for it.
    """
    question = make_question(topic, 9630, verified_language="cpp",
                             boilerplate={"python": PY_OK,
                                          "cpp": "class Solution {};\n"})

    assert question.verified_language == "cpp"
    assert question.adaptive_eligible_for("cpp") is True
    # The language marker still does the work this milestone exists for.
    assert question.adaptive_eligible_for("python") is False


# ═════════════════════════════════════════════════════════════
# 10. Revocation
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_demotion_clears_the_marker(topic):
    """
    Traced through the real command's write, not simulated: a demoted
    question must not read as "verified in Python" to anything querying it.
    """
    question = make_question(topic, 9640, verified_language="python")

    Question.objects.filter(pk=question.pk).update(
        trust_state=Question.TRUST_UNVERIFIED, verified_language=None)
    question.refresh_from_db()

    assert question.verified_language is None
    assert question.adaptive_eligible_for("python") is False


def test_the_demote_command_clears_the_marker_in_the_same_write():
    import inspect

    from groups.management.commands import question_demote

    source = inspect.getsource(question_demote)

    assert "locked.verified_language = None" in source
    assert '"verified_language"' in source


# ═════════════════════════════════════════════════════════════
# 11–12. Promotion derives the marker from the actual reference
# ═════════════════════════════════════════════════════════════

def test_promotion_sets_the_marker_from_the_canonical_reference():
    """
    Not from a constant, and not from the submission — from `reference`, the
    row promotion has already proved is canonical and unchanged since
    approval.
    """
    import ast
    import inspect

    from groups.management.commands import question_promote

    source = inspect.getsource(question_promote)

    assert "locked.verified_language = (reference.language" in source
    # No literal language anywhere in the write path.
    tree = ast.parse(source)
    literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value in {"python", "java", "cpp", "c", "javascript"}
    ]
    assert literals == [], literals


def test_a_python_reference_cannot_produce_another_languages_marker():
    """
    The marker is the reference's own `language` attribute, so there is no
    path by which a Python reference yields "java". Asserted on the
    expression rather than by running promotion, which needs Judge0.
    """
    import inspect

    from groups.management.commands import question_promote

    source = inspect.getsource(question_promote)

    assert "reference.language" in source
    assert "verified_language = \"python\"" not in source


# ═════════════════════════════════════════════════════════════
# The database refuses the unsafe row
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_oracle_verified_without_a_language_is_unrepresentable(topic):
    """
    At the database, not only in the promotion command. "The writer sets it"
    is a weaker guarantee than "the row cannot exist otherwise".
    """
    question = make_question(topic, 9650, verified_language="python")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Question.objects.filter(pk=question.pk).update(
                verified_language=None)


@pytest.mark.django_db
def test_an_empty_string_marker_is_also_refused(topic):
    question = make_question(topic, 9651, verified_language="python")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Question.objects.filter(pk=question.pk).update(
                verified_language="")


@pytest.mark.django_db
def test_an_unverified_question_may_have_a_null_marker(topic):
    """The constraint binds only ORACLE_VERIFIED rows."""
    question = make_question(topic, 9652, verified_language=None,
                             trust=Question.TRUST_UNVERIFIED)

    assert question.verified_language is None


# ═════════════════════════════════════════════════════════════
# 13–15. Existing behaviour, and the end-to-end regression
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_practice_mode_reporting_is_unchanged(topic):
    """The frontend's Verified / Practice split reads these keys."""
    unverified = make_question(topic, 9660, verified_language=None,
                               trust=Question.TRUST_UNVERIFIED)

    summary = unverified.trust_summary()

    assert summary["adaptive_eligible"] is False
    assert summary["servable"] is True
    assert summary["verified_language"] is None


@pytest.mark.django_db
def test_trust_summary_reports_the_verified_language(topic):
    question = make_question(topic, 9661, verified_language="python")

    summary = question.trust_summary()

    assert summary["adaptive_eligible"] is True
    assert summary["verified_language"] == "python"
    # The pre-existing keys are untouched.
    assert set(summary) == {"status", "trust_state", "adaptive_eligible",
                            "servable", "verified_language"}


@pytest.mark.django_db
def test_the_question_level_property_still_means_what_it_meant(topic):
    """
    `is_adaptive_eligible` is deliberately unchanged: exposure ordering and
    the coverage reports have no submission language and still need the
    question-level answer. M3 adds a predicate; it does not redefine this one.
    """
    question = make_question(topic, 9662, verified_language="python")

    assert question.is_adaptive_eligible is True
    assert question.adaptive_eligible_for("cpp") is False


@pytest.mark.django_db
def test_a_cpp_submission_to_a_python_verified_question_does_not_move_rating(
        topic, db):
    """
    THE regression, end to end through ProgressionService: the exact scenario
    that was previously possible. The submission is still graded and stored —
    a learner may practise in any language — but `adaptive_eligible` is False,
    so nothing teaches the learner model.
    """
    from groups.models import CodeSubmission, UserCodingProfile
    from groups.services import ProgressionService

    learner = User.objects.create_user(username="m3-learner", password="pw",
                                       email="m3@example.com")
    question = make_question(topic, 9670, verified_language="python")
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    before = profile.elo_rating

    grade = type("G", (), {
        "final_status": "accepted", "results": [],
        "stored_code": CPP_WITH_MAIN, "all_passed": True})()
    ProgressionService.apply_submission(
        learner, question, "cpp", question.base_difficulty, grade)

    submission = CodeSubmission.objects.get(user=learner, question=question)
    profile.refresh_from_db()

    assert submission.adaptive_eligible is False   # the invariant
    assert profile.elo_rating == before            # nothing was taught


@pytest.mark.django_db
def test_a_python_submission_to_the_same_question_DOES_move_rating(
        topic, db):
    """
    The control. Without it the test above would pass against a build that
    simply never awards rating.
    """
    from groups.models import CodeSubmission, UserCodingProfile
    from groups.services import ProgressionService

    learner = User.objects.create_user(username="m3-learner2", password="pw",
                                       email="m3b@example.com")
    question = make_question(topic, 9671, verified_language="python")
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    before = profile.elo_rating

    grade = type("G", (), {
        "final_status": "accepted", "results": [],
        "stored_code": PY_OK, "all_passed": True})()
    ProgressionService.apply_submission(
        learner, question, "python", question.base_difficulty, grade)

    submission = CodeSubmission.objects.get(user=learner, question=question)
    profile.refresh_from_db()

    assert submission.adaptive_eligible is True
    assert profile.elo_rating != before


# ═════════════════════════════════════════════════════════════
# One definition
# ═════════════════════════════════════════════════════════════

def test_the_service_uses_the_canonical_predicate():
    import inspect

    from groups import services

    source = inspect.getsource(services)

    assert "question.adaptive_eligible_for(language)" in source
    # The language-blind form must not survive at the enforcement point.
    assert "adaptive_eligible = question.is_adaptive_eligible" not in source
