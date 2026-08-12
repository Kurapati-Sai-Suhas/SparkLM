"""
Shadow adaptive model (M2 P2.9a).

UNARMED. The properties this file exists to hold, in priority order:

1. **Production is unaffected.** Elo, mastery, routing and the API response
   are byte-identical whether the shadow model runs, fails, or is deleted.
2. **Only trusted evidence reaches it.** `adaptive_eligible` (P2.7c) AND a
   conceptually evaluable verdict (P2.8b).
3. **No invented history.** A new learner is uncertain, not pre-rated. A
   question's `base_difficulty` is a prior at maximum RD, never calibrated,
   never overwritten.
4. **The arithmetic is right.** Validated against Glickman's own published
   worked example, not against our expectations of it.
"""

import math
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from groups import glicko, shadow
from groups.models import (
    CodeSubmission, CodingPortal, LearnerTopicSkill, Question, QuestionSkill,
    Topic, UserCodingProfile, UserTopicMastery,
)
from groups.services import GradeResult, ProgressionService

User = get_user_model()


# ═════════════════════════════════════════════════════════════
# Glicko-2 arithmetic — pure, no ORM
# ═════════════════════════════════════════════════════════════

def test_matches_glickmans_published_worked_example(monkeypatch):
    """
    The reference check. Glickman's Glicko-2 paper works a single example end
    to end; if our implementation reproduces it, the equations, the volatility
    root-finder and the scale conversions are all right at once.

    The paper centres the scale on 1500; this platform centres on 1200 (an
    offset that cancels in every comparison), so the centre is aligned to the
    paper for this test only.
    """
    monkeypatch.setattr(glicko, "CENTRE", 1500.0)

    rating, rd, sigma = glicko.rate(
        1500, 200, 0.06,
        [(1400, 30, 1.0), (1550, 100, 0.0), (1700, 300, 0.0)], tau=0.5)

    assert rating == pytest.approx(1464.06, abs=0.02)
    assert rd == pytest.approx(151.52, abs=0.02)
    assert sigma == pytest.approx(0.05999, abs=0.00002)


def test_ratings_stay_finite_under_extreme_input():
    for opponent in [(400, 30, 0.0), (3000, 350, 1.0), (1200, 30, 1.0)]:
        rating, rd, sigma = glicko.rate(1200, 350, 0.06, [opponent])
        assert math.isfinite(rating) and math.isfinite(rd) and math.isfinite(sigma)


@pytest.mark.parametrize("rd_in", [glicko.RD_MIN, 100.0, 200.0, glicko.RD_MAX])
def test_rd_stays_within_bounds(rd_in):
    _, rd, _ = glicko.rate(1200, rd_in, 0.06, [(1200, 100, 1.0)])
    assert glicko.RD_MIN <= rd <= glicko.RD_MAX


def test_rd_is_never_negative_after_many_updates():
    rating, rd, sigma = 1200.0, 350.0, 0.06
    for i in range(50):
        rating, rd, sigma = glicko.rate(
            rating, rd, sigma, [(1200, 50, 1.0 if i % 2 else 0.0)])
        assert rd > 0


def test_evidence_reduces_uncertainty():
    _, rd_after, _ = glicko.rate(1200, 350, 0.06, [(1200, 50, 1.0)])
    assert rd_after < 350


def test_inactivity_increases_uncertainty():
    assert glicko.inflate_rd(100.0, 0.06, 30) > 100.0


def test_rd_inflation_is_monotonic_in_elapsed_time():
    values = [glicko.inflate_rd(80.0, 0.06, t) for t in range(0, 200, 10)]
    assert values == sorted(values)


def test_rd_inflation_is_capped():
    assert glicko.inflate_rd(340.0, 0.06, 100000) == glicko.RD_MAX


def test_no_evidence_leaves_the_rating_unchanged():
    rating, rd, sigma = glicko.rate(1337.0, 120.0, 0.06, [], periods_inactive=10)
    assert rating == 1337.0
    assert rd > 120.0          # only uncertainty moved
    assert sigma == 0.06


def test_a_win_raises_and_a_loss_lowers_the_rating():
    up, _, _ = glicko.rate(1200, 200, 0.06, [(1200, 50, 1.0)])
    down, _, _ = glicko.rate(1200, 200, 0.06, [(1200, 50, 0.0)])
    assert up > 1200 > down


def test_repeated_wins_converge_rather_than_diverge():
    rating, rd, sigma = 1200.0, 350.0, 0.06
    steps = []
    for _ in range(25):
        rating, rd, sigma = glicko.rate(rating, rd, sigma, [(1400, 50, 1.0)])
        steps.append(rating)
    gains = [b - a for a, b in zip(steps, steps[1:])]
    assert gains[-1] < gains[0], "updates are not damping as evidence accrues"
    assert rating < 3000


def test_win_probability_is_symmetric_and_bounded():
    p = glicko.win_probability(1400, 50, 1200, 50)
    q = glicko.win_probability(1200, 50, 1400, 50)
    assert 0.0 < p < 1.0 and 0.0 < q < 1.0
    assert p + q == pytest.approx(1.0, abs=1e-9)
    assert p > 0.5 > q


# ═════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def portal(db):
    return CodingPortal.objects.create(name="Shadow Portal")


@pytest.fixture
def topic(portal):
    return Topic.objects.create(name="ShadowTopic", structure_type="flat",
                                portal=portal)


@pytest.fixture
def other_topic(portal):
    return Topic.objects.create(name="OtherTopic", structure_type="flat",
                                portal=portal)


@pytest.fixture
def learner(db):
    return User.objects.create_user(username="shadow-learner",
                                    password="Sh#2026xyz", email="sh@t.com")


def make_question(topic, title="Q", difficulty=1200.0, verified=True):
    question = Question.objects.create(
        title=title, content="c", topic=topic, base_difficulty=difficulty,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "x"}, hidden_wrapper_code={})
    if verified:
        Question.objects.filter(pk=question.pk).update(
            status=Question.STATUS_PUBLISHED,
            trust_state=Question.TRUST_ORACLE_VERIFIED)
        question.refresh_from_db()
    return question


def submit(learner, question, status, passed=0, total=3):
    return ProgressionService.apply_submission(
        user=learner, question=question, language="python",
        difficulty=question.base_difficulty,
        grade=GradeResult(
            stored_code="print(1)", final_status=status,
            passed=passed, total=total,
            results=[{"time": "0.01", "memory": 1000, "status": status}]))


def skill_of(learner, topic):
    return LearnerTopicSkill.objects.filter(user=learner, topic=topic).first()


# ═════════════════════════════════════════════════════════════
# Evidence gating — only trusted, conceptually evaluable verdicts
# ═════════════════════════════════════════════════════════════

def test_an_accepted_verified_submission_updates_the_shadow_model(topic, learner):
    question = make_question(topic)

    submit(learner, question, "accepted", passed=3)

    skill = skill_of(learner, topic)
    assert skill is not None
    assert skill.evidence_count == 1
    assert skill.rating > glicko.DEFAULT_RATING


def test_a_wrong_answer_on_a_verified_question_updates_the_shadow_model(
        topic, learner):
    question = make_question(topic)

    submit(learner, question, "wrong_answer", passed=1)

    skill = skill_of(learner, topic)
    assert skill.evidence_count == 1
    assert skill.rating < glicko.DEFAULT_RATING


def test_an_unverified_submission_cannot_update_the_shadow_model(topic, learner):
    """P2.7c. The verdict may be OUR defect, so it is not evidence."""
    question = make_question(topic, verified=False)

    submission, _, _ = submit(learner, question, "wrong_answer", passed=1)

    assert submission.adaptive_eligible is False
    assert skill_of(learner, topic) is None
    assert QuestionSkill.objects.count() == 0


@pytest.mark.parametrize("status", ["compile_error", "runtime_error", "time_limit"])
def test_an_execution_failure_cannot_update_the_shadow_model(
        topic, learner, status):
    """P2.8b. The program never got as far as being judged on its logic."""
    question = make_question(topic)

    submit(learner, question, status)

    assert skill_of(learner, topic) is None


def test_onboarding_submissions_cannot_update_the_shadow_model(topic, learner):
    """
    Self-report is not measurement. Onboarding writes
    `adaptive_eligible=False`, so the trust gate excludes it without needing a
    special case here.
    """
    make_question(topic, "Onboarding target")

    ProgressionService.apply_onboarding(learner, ["ShadowTopic"])

    assert LearnerTopicSkill.objects.count() == 0
    assert QuestionSkill.objects.count() == 0


def test_a_mixed_history_records_only_the_evidence(topic, learner):
    question = make_question(topic)
    for status in ["accepted", "compile_error", "wrong_answer", "time_limit",
                   "runtime_error", "accepted"]:
        submit(learner, question, status,
               passed=3 if status == "accepted" else 1)

    assert skill_of(learner, topic).evidence_count == 3


# ═════════════════════════════════════════════════════════════
# Production must be unaffected
# ═════════════════════════════════════════════════════════════

def test_the_shadow_model_does_not_change_production_elo(topic, learner):
    question = make_question(topic)

    _, elo_result, profile = submit(learner, question, "accepted", passed=3)

    expected = UserCodingProfile.objects.get(user=learner)
    assert profile.elo_rating == expected.elo_rating == elo_result["new_rating"]
    # and the shadow rating is a DIFFERENT number on its own table
    assert skill_of(learner, topic).rating != expected.elo_rating


def test_the_shadow_model_does_not_change_production_mastery(topic, learner):
    question = make_question(topic)

    submit(learner, question, "accepted", passed=3)

    mastery = UserTopicMastery.objects.get(user=learner, topic=topic)
    assert (mastery.accuracy, mastery.reviews) == (1.0, 1)


def test_a_shadow_failure_cannot_break_a_submission(topic, learner, monkeypatch):
    """
    The property that makes this shadow mode rather than a second production
    path. If the model raises, the learner still gets their verdict.
    """
    question = make_question(topic)

    def explode(*args, **kwargs):
        raise RuntimeError("shadow exploded")

    monkeypatch.setattr(shadow, "apply_submission", explode)

    submission, elo_result, profile = submit(
        learner, question, "accepted", passed=3)

    assert submission.pk is not None
    assert elo_result["rating_change"] > 0
    assert UserTopicMastery.objects.get(user=learner, topic=topic).reviews == 1
    assert skill_of(learner, topic) is None


def test_production_selection_is_untouched_by_shadow_state(topic, learner):
    """
    `_select_question` must return the same question whether or not shadow
    rows exist. The shadow model is not allowed to leak into routing.
    """
    from groups.coding_views import _select_question

    easy = make_question(topic, "Easy", 1000.0)
    mid = make_question(topic, "Mid", 1200.0)
    before = _select_question(learner, topic.name, 1200.0)

    # Give the shadow model a wildly different opinion.
    QuestionSkill.objects.create(question=easy, rating=9999.0,
                                 prior_rating=1000.0, rating_deviation=30.0)
    QuestionSkill.objects.create(question=mid, rating=-9999.0,
                                 prior_rating=1200.0, rating_deviation=30.0)
    LearnerTopicSkill.objects.create(user=learner, topic=topic, rating=9999.0,
                                     rating_deviation=30.0)

    after = _select_question(learner, topic.name, 1200.0)

    assert before.pk == after.pk == mid.pk


# ═════════════════════════════════════════════════════════════
# Cold start — no invented history
# ═════════════════════════════════════════════════════════════

def test_a_new_learner_starts_uncertain_not_rated(topic, learner):
    rating, rd, evidence = shadow.current_ability(learner, topic)

    assert rating == glicko.DEFAULT_RATING
    assert rd == glicko.DEFAULT_RD == 350.0
    assert evidence == 0


def test_a_new_question_takes_base_difficulty_as_a_prior_only(topic, learner):
    question = make_question(topic, "Prior", 1600.0)

    submit(learner, question, "accepted", passed=3)

    q_skill = QuestionSkill.objects.get(question=question)
    assert q_skill.prior_rating == 1600.0
    assert q_skill.rating != 1600.0, "the prior was treated as calibrated"
    question.refresh_from_db()
    assert question.base_difficulty == 1600.0, "base_difficulty was overwritten"


def test_a_new_question_starts_at_maximum_uncertainty(topic, learner):
    question = make_question(topic, "Fresh", 1300.0)

    q_skill = shadow.get_or_create_question_skill(question)

    assert q_skill.rating_deviation == glicko.DEFAULT_RD
    assert q_skill.rating == q_skill.prior_rating == 1300.0


def test_no_shadow_state_is_created_without_evidence(topic, learner):
    make_question(topic)
    assert LearnerTopicSkill.objects.count() == 0
    assert QuestionSkill.objects.count() == 0


# ═════════════════════════════════════════════════════════════
# Per-topic isolation
# ═════════════════════════════════════════════════════════════

def test_evidence_in_one_topic_cannot_move_another(topic, other_topic, learner):
    submit(learner, make_question(topic, "A"), "accepted", passed=3)
    submit(learner, make_question(other_topic, "B"), "wrong_answer", passed=1)

    a = skill_of(learner, topic)
    b = skill_of(learner, other_topic)
    assert a.rating > glicko.DEFAULT_RATING
    assert b.rating < glicko.DEFAULT_RATING
    assert a.evidence_count == b.evidence_count == 1


def test_a_learner_can_be_strong_in_one_topic_and_weak_in_another(
        topic, other_topic, learner):
    strong = make_question(topic, "Strong", 1400.0)
    weak = make_question(other_topic, "Weak", 1400.0)
    for _ in range(5):
        submit(learner, strong, "accepted", passed=3)
        submit(learner, weak, "wrong_answer", passed=1)

    assert skill_of(learner, topic).rating > skill_of(learner, other_topic).rating
    # The production model cannot represent this at all — one global scalar.
    assert UserCodingProfile.objects.filter(user=learner).count() == 1


# ═════════════════════════════════════════════════════════════
# Two-sided: the question moves too
# ═════════════════════════════════════════════════════════════

def test_the_question_rating_moves_with_the_learner(topic, learner):
    question = make_question(topic, "Two sided", 1200.0)

    submit(learner, question, "wrong_answer", passed=1)

    q_skill = QuestionSkill.objects.get(question=question)
    assert q_skill.rating > 1200.0, "a question that beat a learner got no harder"
    assert skill_of(learner, topic).rating < 1200.0


def test_a_question_everyone_solves_becomes_easier(topic, portal):
    question = make_question(topic, "Easy in practice", 1500.0)
    for i in range(6):
        u = User.objects.create_user(username=f"solver{i}", email=f"s{i}@t.com",
                                     password="Sh#2026xyz")
        submit(u, question, "accepted", passed=3)

    assert QuestionSkill.objects.get(question=question).rating < 1500.0


def test_question_uncertainty_falls_as_evidence_accumulates(topic, portal):
    question = make_question(topic, "Measured", 1200.0)
    rds = []
    for i in range(6):
        u = User.objects.create_user(username=f"m{i}", email=f"m{i}@t.com",
                                     password="Sh#2026xyz")
        submit(u, question, "accepted" if i % 2 else "wrong_answer",
               passed=3 if i % 2 else 1)
        rds.append(QuestionSkill.objects.get(question=question).rating_deviation)

    assert rds[-1] < rds[0]


# ═════════════════════════════════════════════════════════════
# Forgetting via RD inflation
# ═════════════════════════════════════════════════════════════

def test_inactivity_inflates_a_learners_uncertainty(topic, learner):
    question = make_question(topic)
    for _ in range(4):
        submit(learner, question, "accepted", passed=3)
    confident = skill_of(learner, topic)
    rd_before = confident.rating_deviation

    later = timezone.now() + timedelta(days=120)
    _, rd_after, _ = shadow.current_ability(learner, topic, now=later)

    assert rd_after > rd_before
    assert glicko.RD_MIN <= rd_after <= glicko.RD_MAX


def test_the_rating_itself_does_not_drift_during_inactivity(topic, learner):
    question = make_question(topic)
    submit(learner, question, "accepted", passed=3)
    rating_before = skill_of(learner, topic).rating

    rating_after, _, _ = shadow.current_ability(
        learner, topic, now=timezone.now() + timedelta(days=365))

    assert rating_after == rating_before


def test_reading_a_learner_does_not_age_them(topic, learner):
    """Inflation is computed for the caller, never persisted by a read."""
    question = make_question(topic)
    submit(learner, question, "accepted", passed=3)
    stored = skill_of(learner, topic).rating_deviation

    shadow.current_ability(learner, topic,
                           now=timezone.now() + timedelta(days=90))

    assert skill_of(learner, topic).rating_deviation == stored


# ═════════════════════════════════════════════════════════════
# Thompson sampling / shadow selection
# ═════════════════════════════════════════════════════════════

def test_selection_is_deterministic_for_a_fixed_seed(topic, learner):
    for i, d in enumerate([1000.0, 1200.0, 1400.0, 1600.0]):
        make_question(topic, f"Q{i}", d)
    candidates = list(Question.objects.filter(topic=topic))

    picks = {shadow.select_question(learner, topic, candidates, seed=42)[0].pk
             for _ in range(10)}

    assert len(picks) == 1


def test_different_seeds_can_explore_different_questions(topic, learner):
    """
    A high-RD learner should explore. With RD 350 the sampled ability moves a
    lot, so different seeds should reach different questions — that is the
    mechanism production's deterministic argmin cannot express.
    """
    for i, d in enumerate([800.0, 1000.0, 1200.0, 1400.0, 1600.0, 1800.0]):
        make_question(topic, f"Q{i}", d)
    candidates = list(Question.objects.filter(topic=topic))

    picks = {shadow.select_question(learner, topic, candidates, seed=s)[0].pk
             for s in range(30)}

    assert len(picks) > 1, "a maximally uncertain learner never explored"


def test_a_confident_learner_explores_far_less(topic, learner):
    """The other half: exploration is proportional to ignorance."""
    for i, d in enumerate([800.0, 1000.0, 1200.0, 1400.0, 1600.0, 1800.0]):
        make_question(topic, f"Q{i}", d)
    candidates = list(Question.objects.filter(topic=topic))
    uncertain = {shadow.select_question(learner, topic, candidates, seed=s)[0].pk
                 for s in range(30)}

    LearnerTopicSkill.objects.create(
        user=learner, topic=topic, rating=1200.0,
        rating_deviation=glicko.RD_MIN, evidence_count=50,
        last_evidence_at=timezone.now())
    confident = {shadow.select_question(learner, topic, candidates, seed=s)[0].pk
                 for s in range(30)}

    assert len(confident) < len(uncertain)


def test_the_observation_explains_the_decision(topic, learner):
    question = make_question(topic, "Explained", 1250.0)

    chosen, observation = shadow.select_question(
        learner, topic, [question], seed=1)

    assert chosen.pk == question.pk
    data = observation.as_dict()
    for field in ("timestamp", "user_id", "topic", "learner_rating",
                  "learner_rd", "sampled_ability", "shadow_question_id",
                  "shadow_question_rating", "shadow_question_rd",
                  "difficulty_distance", "predicted_success",
                  "candidate_count", "seed", "reason"):
        assert field in data, f"observation is missing {field}"
    assert data["candidate_count"] == 1
    assert 0.0 <= data["predicted_success"] <= 1.0


def test_the_observation_leaks_no_content(topic, learner):
    question = make_question(topic, "Secret Title", 1250.0)
    question.hidden_test_cases = [{"stdin": "SECRET_IN",
                                   "expected_output": "SECRET_OUT"}]
    question.save(update_fields=["hidden_test_cases"])

    _, observation = shadow.select_question(learner, topic, [question], seed=1)

    blob = str(observation.as_dict())
    assert "SECRET_IN" not in blob and "SECRET_OUT" not in blob
    assert "Secret Title" not in blob


def test_selection_with_no_candidates_is_reported_not_crashed(topic, learner):
    chosen, observation = shadow.select_question(learner, topic, [], seed=1)

    assert chosen is None
    assert observation.candidate_count == 0
    assert observation.reason == "no candidates"


def test_a_question_without_shadow_state_uses_its_prior(topic, learner):
    question = make_question(topic, "Unrated", 1450.0)

    _, observation = shadow.select_question(learner, topic, [question], seed=3)

    assert observation.shadow_question_rating == 1450.0
    assert observation.shadow_question_rd == glicko.DEFAULT_RD


# ═════════════════════════════════════════════════════════════
# The comparison command
# ═════════════════════════════════════════════════════════════

def test_the_shadow_report_runs_with_no_state(db, capsys):
    from django.core.management import call_command

    call_command("shadow_report")

    assert "No shadow learner state exists yet" in capsys.readouterr().out


def test_the_shadow_report_compares_without_changing_anything(
        topic, learner, capsys):
    from django.core.management import call_command

    question = make_question(topic, "Compared", 1200.0)
    submit(learner, question, "accepted", passed=3)
    before = (skill_of(learner, topic).rating,
              UserCodingProfile.objects.get(user=learner).elo_rating,
              UserTopicMastery.objects.get(user=learner, topic=topic).accuracy)

    call_command("shadow_report", "--json", "--seed", "5")

    after = (skill_of(learner, topic).rating,
             UserCodingProfile.objects.get(user=learner).elo_rating,
             UserTopicMastery.objects.get(user=learner, topic=topic).accuracy)
    assert before == after
    assert "agreement_rate" in capsys.readouterr().out


# ═════════════════════════════════════════════════════════════
# Invariants found by mutation testing
# ═════════════════════════════════════════════════════════════

def test_the_two_sides_are_updated_from_the_SAME_snapshot(topic, learner):
    """
    Both updates must read the state as it was BEFORE either moved.

    Feeding the learner's freshly-raised rating into the question's update
    counts one piece of evidence twice: the question would be judged to have
    lost to a stronger opponent than the one it actually faced, so it would
    drop less than it should. Mutation testing caught that nothing detected it.

    With both sides starting identical, a win must move them by equal and
    opposite amounts. Any asymmetry means one update saw the other's result.
    """
    question = make_question(topic, "Symmetric", glicko.DEFAULT_RATING)
    # Force both sides to identical starting state.
    shadow.get_or_create_question_skill(question)
    QuestionSkill.objects.filter(question=question).update(
        rating=glicko.DEFAULT_RATING, rating_deviation=glicko.DEFAULT_RD,
        volatility=glicko.DEFAULT_VOLATILITY)
    LearnerTopicSkill.objects.create(
        user=learner, topic=topic, rating=glicko.DEFAULT_RATING,
        rating_deviation=glicko.DEFAULT_RD,
        volatility=glicko.DEFAULT_VOLATILITY)

    submit(learner, question, "accepted", passed=3)

    learner_gain = skill_of(learner, topic).rating - glicko.DEFAULT_RATING
    question_loss = glicko.DEFAULT_RATING - QuestionSkill.objects.get(
        question=question).rating

    assert learner_gain > 0 and question_loss > 0
    assert learner_gain == pytest.approx(question_loss, abs=0.5), (
        f"asymmetric update — learner +{learner_gain:.2f} but question "
        f"-{question_loss:.2f}; one side saw the other's new rating")


@pytest.mark.parametrize("rd_in,expected", [
    (5.0, glicko.RD_MIN),
    (0.0, glicko.RD_MIN),
    (glicko.RD_MIN, glicko.RD_MIN),
])
def test_rd_inflation_enforces_the_lower_bound(rd_in, expected):
    """
    `inflate_rd` is a public function and clamps BOTH ways. The floor is
    unreachable through the sanctioned update path — `rate()` already clamps
    its output — but a defensive bound that is never asserted is a bound that
    silently disappears.
    """
    assert glicko.inflate_rd(rd_in, glicko.DEFAULT_VOLATILITY, 0) == expected
    assert glicko.inflate_rd(rd_in, glicko.DEFAULT_VOLATILITY, 5) >= glicko.RD_MIN


def test_selection_does_not_depend_on_candidate_iteration_order(topic, learner):
    """
    The terminal question-id key makes the ordering TOTAL. Without it, two
    questions at the same rating are separated only by whichever the loop
    happened to see first — so the answer would depend on the caller's
    queryset order rather than on the model.
    """
    first = make_question(topic, "Tied A", 1200.0)
    second = make_question(topic, "Tied B", 1200.0)
    for q in (first, second):
        QuestionSkill.objects.create(question=q, rating=1200.0,
                                     prior_rating=1200.0,
                                     rating_deviation=glicko.DEFAULT_RD)

    forwards, _ = shadow.select_question(
        learner, topic, [first, second], seed=11)
    backwards, _ = shadow.select_question(
        learner, topic, [second, first], seed=11)

    assert forwards.pk == backwards.pk == min(first.pk, second.pk), (
        "selection changed with candidate order — the tie-break is not total")
