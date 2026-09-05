"""
Trust-aware recommendation exposure (M2 P2.31).

The recommender was trust-blind: six verified questions in a servable pool of
1,788 is 0.336%, so across every recommendation ever logged the expected
number landing on verified content was 0.73 and the observed number was zero.
No adaptive-eligible submission has ever existed, so Traffic Cop has never
received a trustworthy outcome.

Trust now enters the ordering. The tests that matter most are NOT "a trusted
question is preferred" — that is the easy half. They are the ones proving
what the change does NOT do: it does not filter, does not override difficulty,
does not resurrect solved questions, does not touch routing, and does not
reclassify a single historical row.

Local/synthetic database only.
"""

import inspect
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from groups import coding_views as cv
from groups.models import (
    CodeSubmission, CodingPortal, Question, RecommendationLog, Topic,
)

User = get_user_model()

#: The default a fresh UserCodingProfile carries, and where 17 of the 20
#: production learners actually sit.
DEFAULT_ELO = 1200.0


@pytest.fixture
def portal(db):
    return CodingPortal.objects.create(name="Exposure Portal")


@pytest.fixture
def topic(db, portal):
    made, _ = Topic.objects.get_or_create(
        name="ExposureTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return made


@pytest.fixture
def other_topic(db, portal):
    made, _ = Topic.objects.get_or_create(
        name="OtherExposureTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return made


@pytest.fixture
def learner(db):
    return User.objects.create_user(username="exp-learner", password="pw",
                                    email="e@example.com")


def make_question(topic, question_id, *, difficulty, verified,
                  content="Statement."):
    """A question at a real rung of the bank's difficulty ladder."""
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}", content=content, topic=topic,
        base_difficulty=difficulty,
        status=(Question.STATUS_PUBLISHED if verified
                else Question.STATUS_DRAFT),
        trust_state=(Question.TRUST_ORACLE_VERIFIED if verified
                     else Question.TRUST_UNVERIFIED),
        boilerplate_code={"python": "def f(): pass\n"},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v1")


def select(learner, topic, elo=DEFAULT_ELO, now=None):
    return cv._select_question(learner, topic.name, elo, now=now)


# ═════════════════════════════════════════════════════════════
# A — a trusted candidate is preferred
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_production_shape_now_serves_trusted_content(learner, topic):
    """
    Exactly the case that made the loop unbootstrappable: five of six verified
    questions sit at 1000, the bulk of the bank sits at 1300, and the learner
    sits at the 1200 default. Under the old ordering |1300-1200| = 100 beat
    |1000-1200| = 200 and trust was never consulted.
    """
    trusted = make_question(topic, 8000, difficulty=1000.0, verified=True)
    make_question(topic, 8001, difficulty=1300.0, verified=False)

    assert select(learner, topic) == trusted


@pytest.mark.django_db
def test_a_trusted_question_wins_an_exact_difficulty_tie(learner, topic):
    make_question(topic, 8010, difficulty=1300.0, verified=False)
    trusted = make_question(topic, 8011, difficulty=1300.0, verified=True)

    assert select(learner, topic) == trusted


@pytest.mark.django_db
def test_trust_beats_a_lower_question_id(learner, topic):
    """The terminal `id` key must not silently outrank trust."""
    make_question(topic, 1, difficulty=1300.0, verified=False)
    trusted = make_question(topic, 9999, difficulty=1300.0, verified=True)

    assert select(learner, topic) == trusted


# ═════════════════════════════════════════════════════════════
# B — fallback survives; the pool never collapses
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_with_no_trusted_candidate_the_old_selection_still_works(
        learner, topic):
    nearest = make_question(topic, 8020, difficulty=1300.0, verified=False)
    make_question(topic, 8021, difficulty=1600.0, verified=False)

    assert select(learner, topic) == nearest


@pytest.mark.django_db
def test_trust_is_an_ordering_term_not_a_filter(learner, topic):
    """
    The candidate SET is unchanged — 1,782 unverified questions stay
    reachable. A filter would have cut the pool to six and collapsed the
    product; this is the same "demote, never exclude" shape the cooldown uses.
    """
    for offset in range(5):
        make_question(topic, 8030 + offset, difficulty=1300.0, verified=False)

    assert cv._candidate_questions(learner, topic_name=topic.name).count() == 5


@pytest.mark.django_db
def test_a_topic_with_no_trusted_question_is_entirely_unaffected(
        learner, other_topic):
    """15 of 20 production topics hold no verified question at all."""
    first = make_question(other_topic, 8040, difficulty=1300.0, verified=False)
    make_question(other_topic, 8041, difficulty=1600.0, verified=False)

    assert select(learner, other_topic) == first


# ═════════════════════════════════════════════════════════════
# C — difficulty is not distorted
#
# The safety argument for the whole change.
# ═════════════════════════════════════════════════════════════

def _drain(learner, topic, elo=DEFAULT_ELO):
    """
    The order `_select_question` ACTUALLY serves, by taking each pick and
    marking it solved so the next one surfaces.

    Driving the real function matters. An earlier version of this test rebuilt
    the `order_by` by hand and compared that to the old keys — which passed
    happily when the production `elo_diff` key was deleted, because the test
    was never looking at production. Restating the thing under test is exactly
    the mistake this module's own docstrings warn about.
    """
    served = []
    while True:
        question = cv._select_question(learner, topic.name, elo)
        if question is None:
            return served
        served.append(question.pk)
        CodeSubmission.objects.create(
            user=learner, question=question, language="python", code="x",
            status="accepted", adaptive_eligible=False)


@pytest.mark.django_db
def test_untrusted_ordering_is_unchanged_by_the_new_policy(learner, topic):
    """
    `floor(d / 300)` is monotonic in d, so for questions of EQUAL trust the
    pair (band, diff) orders exactly as diff alone did.

    The difficulties are chosen to span band boundaries in BOTH directions at
    the 1200 default: 1200 (diff 0, band 0), 1300 (100, band 0), 1000 (200,
    band 0), 1600 (400, band 1), 700 (500, band 1). If the exact-difficulty
    key were dropped, 1300/1000/1200 would tie inside band 0 and fall through
    to id order — which is a different sequence, and this notices.
    """
    difficulties = {8050: 1200.0, 8051: 1300.0, 8052: 1000.0,
                    8053: 1600.0, 8054: 700.0}
    for question_id, difficulty in difficulties.items():
        make_question(topic, question_id, difficulty=difficulty,
                      verified=False)

    served = _drain(learner, topic)

    expected = sorted(difficulties,
                      key=lambda q: (abs(difficulties[q] - DEFAULT_ELO), q))
    assert served == expected


@pytest.mark.django_db
def test_the_exact_difficulty_key_still_separates_questions_in_one_band(
        learner, topic):
    """
    Guards the mutant the test above was written to catch: all three of these
    share band 0, so only the exact difference can order them correctly.
    """
    make_question(topic, 8058, difficulty=1000.0, verified=False)   # diff 200
    make_question(topic, 8057, difficulty=1300.0, verified=False)   # diff 100
    make_question(topic, 8059, difficulty=1200.0, verified=False)   # diff 0

    assert _drain(learner, topic) == [8059, 8057, 8058]


@pytest.mark.django_db
def test_a_trusted_question_in_a_worse_band_does_not_win(learner, topic):
    """
    Trust preference is BOUNDED. A verified question two rungs away must not
    be forced on a learner just because it is verified — that would be trust
    overriding difficulty, which property C forbids.
    """
    make_question(topic, 8060, difficulty=1300.0, verified=False)   # band 0
    make_question(topic, 8061, difficulty=1600.0, verified=True)    # band 1

    chosen = select(learner, topic)

    assert chosen.pk == 8060
    assert not chosen.is_adaptive_eligible


@pytest.mark.django_db
def test_a_far_trusted_question_loses_to_a_near_untrusted_one(learner, topic):
    make_question(topic, 8070, difficulty=1200.0, verified=False)   # diff 0
    make_question(topic, 8071, difficulty=1600.0, verified=True)    # diff 400

    assert select(learner, topic).pk == 8070


@pytest.mark.django_db
def test_within_a_band_trust_wins_but_across_bands_difficulty_wins(
        learner, topic):
    """Both halves of the policy in one place, so neither can drift alone."""
    make_question(topic, 8080, difficulty=1300.0, verified=False)   # band 0
    trusted_near = make_question(topic, 8081, difficulty=1000.0,
                                 verified=True)                     # band 0
    make_question(topic, 8082, difficulty=1600.0, verified=True)    # band 1

    assert select(learner, topic) == trusted_near


# ═════════════════════════════════════════════════════════════
# D/E — existing guards remain intact
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_solved_trusted_question_is_never_re_served(learner, topic):
    """`solved=False` is the one hard exclusion and trust does not lift it."""
    trusted = make_question(topic, 8090, difficulty=1300.0, verified=True)
    fallback = make_question(topic, 8091, difficulty=1300.0, verified=False)
    CodeSubmission.objects.create(user=learner, question=trusted,
                                  language="python", code="x",
                                  status="accepted", adaptive_eligible=True)

    assert select(learner, topic) == fallback


@pytest.mark.django_db
def test_a_trusted_question_in_cooldown_still_loses_to_a_fresh_trusted_one(
        learner, topic):
    """Trust does not outrank the recently-failed demotion among equals."""
    now = timezone.now()
    cooling = make_question(topic, 8100, difficulty=1300.0, verified=True)
    fresh = make_question(topic, 8101, difficulty=1300.0, verified=True)
    submission = CodeSubmission.objects.create(
        user=learner, question=cooling, language="python", code="x",
        status="wrong_answer", adaptive_eligible=True)
    CodeSubmission.objects.filter(pk=submission.pk).update(
        submitted_at=now - timedelta(hours=1))

    assert select(learner, topic, now=now) == fresh


@pytest.mark.django_db
def test_the_topic_filter_is_still_respected(learner, topic, other_topic):
    """Trust must not pull a question across a topic boundary."""
    make_question(other_topic, 8110, difficulty=1200.0, verified=True)
    in_topic = make_question(topic, 8111, difficulty=1600.0, verified=False)

    assert select(learner, topic) == in_topic


@pytest.mark.django_db
def test_selection_is_deterministic_across_repeated_calls(learner, topic):
    for index, difficulty in enumerate((1000.0, 1300.0, 1300.0, 1600.0)):
        make_question(topic, 8120 + index, difficulty=difficulty,
                      verified=(index == 2))

    picks = {select(learner, topic).pk for _ in range(5)}

    assert len(picks) == 1


# ═════════════════════════════════════════════════════════════
# F/G — Traffic Cop and Elo are untouched
# ═════════════════════════════════════════════════════════════

def test_the_selector_never_touches_the_routing_decision():
    source = inspect.getsource(cv._select_question) + inspect.getsource(
        cv._candidate_questions)
    for forbidden in ("decide_route", "predict_route", "RoutingClassifier",
                      "OSCILLATION_Z_THRESHOLD", "ACCURACY_THRESHOLD"):
        assert forbidden not in source, forbidden


@pytest.mark.django_db
def test_routing_telemetry_is_unchanged_by_the_new_ordering(learner, topic):
    from groups.hybrid_router import compute_routing_telemetry

    make_question(topic, 8130, difficulty=1300.0, verified=True)
    before = compute_routing_telemetry(learner)
    select(learner, topic)

    assert compute_routing_telemetry(learner) == before


@pytest.mark.django_db
def test_target_elo_still_drives_the_band(learner, topic):
    """Elo is still the axis; the band is computed from it, not around it."""
    make_question(topic, 8140, difficulty=1000.0, verified=False)
    make_question(topic, 8141, difficulty=1600.0, verified=False)

    assert select(learner, topic, elo=1000.0).pk == 8140
    assert select(learner, topic, elo=1600.0).pk == 8141


# ═════════════════════════════════════════════════════════════
# H — one definition of adaptive eligibility
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_orm_predicate_and_the_property_agree(db, topic):
    """
    The ordering needed the trust rule in SQL, which risked a SECOND
    definition. Checked across every status × trust_state combination the
    database will actually hold, rather than by inspection.

    `DRAFT + ORACLE_VERIFIED` is skipped because it is not merely absent from
    production, it is UNREPRESENTABLE: the check constraint
    `question_draft_cannot_be_oracle_verified` rejects it. Writing the test
    against the full cartesian product failed on that constraint, which is the
    database agreeing with the invariant rather than a gap in coverage.
    """
    made = []
    forbidden = (Question.STATUS_DRAFT, Question.TRUST_ORACLE_VERIFIED)
    for index, status in enumerate(
            [c[0] for c in Question.STATUS_CHOICES]):
        for offset, trust in enumerate(
                [c[0] for c in Question.TRUST_CHOICES]):
            if (status, trust) == forbidden:
                continue
            question = make_question(topic, 8200 + index * 10 + offset,
                                     difficulty=1300.0, verified=False)
            Question.objects.filter(pk=question.pk).update(
                status=status, trust_state=trust)
            made.append(question.pk)

    assert len(made) >= 3, "the state space collapsed; the test proves nothing"

    by_orm = set(Question.objects.filter(Question.adaptive_eligible_q())
                 .values_list("id", flat=True))
    by_property = {q.pk for q in Question.objects.filter(pk__in=made)
                   if q.is_adaptive_eligible}

    assert by_orm == by_property


@pytest.mark.django_db
def test_an_unverified_question_never_reports_as_adaptive_eligible(
        learner, topic):
    question = make_question(topic, 8210, difficulty=1300.0, verified=False)
    chosen = select(learner, topic)

    assert chosen == question
    assert chosen.is_adaptive_eligible is False
    assert chosen.trust_summary()["adaptive_eligible"] is False


# ═════════════════════════════════════════════════════════════
# I/J — exposure telemetry, and history stays put
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_exposure_trust_is_frozen_at_write_time_not_derived(learner, topic):
    """
    The property that stops history being rewritten. A row records what the
    learner was SHOWN; verifying the question afterwards must not convert a
    past impression into a trusted exposure.
    """
    question = make_question(topic, 8220, difficulty=1300.0, verified=False)
    log = RecommendationLog.objects.create(
        user=learner, recommended_topic=topic, engine_used="flat",
        problem_id=str(question.pk),
        served_adaptive_eligible=question.is_adaptive_eligible)

    question.status = Question.STATUS_PUBLISHED
    question.trust_state = Question.TRUST_ORACLE_VERIFIED
    question.save(update_fields=["status", "trust_state"])

    log.refresh_from_db()
    assert log.served_adaptive_eligible is False       # not True


@pytest.mark.django_db
def test_a_pre_instrumentation_row_stays_unknown_not_untrusted(
        learner, topic):
    """
    Null means "served before the field existed". Folding it into either
    bucket would reclassify 218 production rows.
    """
    from groups import routing_readiness as rr

    question = make_question(topic, 8230, difficulty=1300.0, verified=False)
    RecommendationLog.objects.create(
        user=learner, recommended_topic=topic, engine_used="flat",
        problem_id=str(question.pk), served_adaptive_eligible=None)

    census = rr.collect_census()

    assert census.exposures_before_instrumentation == 1
    assert census.trusted_exposures == 0
    assert census.untrusted_exposures == 0


@pytest.mark.django_db
def test_the_exposure_rate_excludes_unknown_rows_from_its_denominator(
        learner, topic):
    """
    Otherwise the trusted rate would appear to fall as an artefact of old
    data rather than describing current serving.
    """
    from groups import routing_readiness as rr

    question = make_question(topic, 8240, difficulty=1300.0, verified=True)
    for served in (True, False, None, None, None):
        RecommendationLog.objects.create(
            user=learner, recommended_topic=topic, engine_used="flat",
            problem_id=str(question.pk), served_adaptive_eligible=served)

    census = rr.collect_census()

    assert census.trusted_exposure_rate == pytest.approx(0.5)   # 1 of 2


@pytest.mark.django_db
def test_never_exposed_trusted_questions_are_counted(learner, topic):
    from groups import routing_readiness as rr

    seen = make_question(topic, 8250, difficulty=1300.0, verified=True)
    make_question(topic, 8251, difficulty=1300.0, verified=True)   # never
    RecommendationLog.objects.create(
        user=learner, recommended_topic=topic, engine_used="flat",
        problem_id=str(seen.pk), served_adaptive_eligible=True)

    census = rr.collect_census()

    assert census.oracle_verified_questions == 2
    assert census.trusted_questions_never_exposed == 1
    assert census.learners_reached_by_trusted_content == 1


@pytest.mark.django_db
def test_exposure_telemetry_does_not_make_a_contaminated_label_trustworthy(
        learner, topic):
    """
    A recommendation is an impression, not an outcome. Serving trusted content
    must not, by itself, produce a trustworthy decision→outcome pair.
    """
    from groups import routing_readiness as rr

    question = make_question(topic, 8260, difficulty=1300.0, verified=True)
    RecommendationLog.objects.create(
        user=learner, recommended_topic=topic, engine_used="flat",
        problem_id=str(question.pk), served_adaptive_eligible=True,
        actual_result_correct=True)          # closed, but no submission exists

    census = rr.collect_census()

    assert census.trusted_exposures == 1
    assert census.decisions_closed == 1
    assert census.decisions_trustworthy == 0       # the point
    assert census.decisions_contaminated == 1


# ═════════════════════════════════════════════════════════════
# K — the selection path writes nothing
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_selecting_a_question_mutates_nothing(learner, topic):
    make_question(topic, 8270, difficulty=1000.0, verified=True)
    make_question(topic, 8271, difficulty=1300.0, verified=False)

    def snapshot():
        return {
            "questions": list(Question.objects.order_by("pk").values()),
            "submissions": list(CodeSubmission.objects.order_by("pk").values()),
            "decisions": list(RecommendationLog.objects.order_by("pk").values()),
        }

    before = snapshot()
    select(learner, topic)
    assert snapshot() == before


def test_the_selector_contains_no_write_verb():
    source = inspect.getsource(cv._select_question) + inspect.getsource(
        cv._candidate_questions) + inspect.getsource(cv._difficulty_band)
    for verb in (".save(", ".create(", ".update(", ".delete("):
        assert verb not in source, verb


# ═════════════════════════════════════════════════════════════
# L — the agent path is unaffected
# ═════════════════════════════════════════════════════════════

def test_the_agent_validator_still_bounds_on_servability_not_ordering():
    """
    The agent validates candidates against `_servable_questions()`; it does
    not select through `_select_question`. The ordering change must not have
    reached it.
    """
    from groups.agent import tools

    source = inspect.getsource(tools)
    assert "_servable_questions" in source
    assert "_select_question" not in source
    assert "elo_band" not in source


@pytest.mark.django_db
def test_a_trusted_question_is_still_servable_for_the_agent(learner, topic):
    question = make_question(topic, 8280, difficulty=1300.0, verified=True)

    assert cv._servable_questions().filter(pk=question.pk).exists()


# ═════════════════════════════════════════════════════════════
# M/N — the trust contract the frontend reads is unchanged
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_trust_summary_shape_is_unchanged(learner, topic):
    question = make_question(topic, 8290, difficulty=1300.0, verified=True)

    summary = question.trust_summary()

    assert set(summary) == {"status", "trust_state", "adaptive_eligible",
                            "servable"}
    assert summary["adaptive_eligible"] is True
    assert summary["servable"] is True


@pytest.mark.django_db
def test_trust_summary_exposes_no_grading_truth(learner, topic):
    question = make_question(topic, 8300, difficulty=1300.0, verified=True)
    question.hidden_test_cases = [
        {"stdin": "SENTINEL-IN", "expected_output": "SENTINEL-OUT"}]
    question.save(update_fields=["hidden_test_cases"])

    assert "SENTINEL" not in str(question.trust_summary())


def test_the_band_constant_matches_the_banks_difficulty_ladder():
    """
    EXPOSURE_ELO_BAND is the spacing of the repository's own DIFFICULTY_BANDS,
    not a number chosen by feel. If those rungs move, this must be revisited
    rather than silently left behind.
    """
    from groups.reseed_generation import DIFFICULTY_BANDS

    rungs = sorted(DIFFICULTY_BANDS)
    spacings = {rungs[i + 1] - rungs[i] for i in range(len(rungs) - 1)}

    assert spacings == {cv.EXPOSURE_ELO_BAND}
