"""
Shadow adaptive model (M2 P2.9a).

UNARMED. Nothing here reaches a learner. `NextProblemView` still selects with
the P2.8a ordering; `EloEngine` still owns the rating the UI shows; mastery is
untouched. This runs beside production on the same evidence so the two can be
compared before anything is promoted.

Three rules the rest of this module exists to keep:

1. **Only trusted evidence.** `adaptive_eligible` (P2.7c) AND a conceptually
   evaluable verdict (P2.8b). A compile error is not evidence about an
   algorithm, and an unverified question's verdict may be our defect rather
   than the learner's mistake.
2. **Never affect production.** Every entry point is wrapped so that a failure
   here rolls back only its own writes and is logged, never raised.
3. **Never invent history.** A new learner is high-uncertainty, not
   pre-rated. A question's `base_difficulty` seeds a PRIOR at maximum
   uncertainty; it is never treated as calibrated and never overwritten.
"""

import logging
import random
from dataclasses import asdict, dataclass
from typing import Optional

from django.db import transaction
from django.utils import timezone

from groups import glicko, glicko_history
from groups.models import LearnerTopicSkill, Question, QuestionSkill
from groups.services import LEARNER_EVIDENCE_STATUSES

logger = logging.getLogger(__name__)

#: Where the shadow aims. Desirable difficulty: a learner should succeed often
#: enough to stay engaged and fail often enough to learn. 0.7 is a convention
#: from the mastery-learning literature, NOT a measured value for this
#: platform — there is no data to fit it to yet.
TARGET_SUCCESS_PROBABILITY = 0.7


@dataclass
class ShadowObservation:
    """
    Machine-readable record of one shadow decision.

    Carries no source code, no hidden tests, no expected outputs and no PII —
    ids and numbers only, because this is designed to be logged.
    """

    timestamp: str
    user_id: int
    topic: str
    learner_rating: float
    learner_rd: float
    learner_evidence_count: int
    sampled_ability: float
    production_question_id: Optional[int]
    shadow_question_id: Optional[int]
    shadow_question_rating: Optional[float]
    shadow_question_rd: Optional[float]
    agree: bool
    difficulty_distance: Optional[float]
    predicted_success: Optional[float]
    candidate_count: int
    seed: int
    reason: str

    def as_dict(self):
        return asdict(self)


def is_shadow_evidence(submission):
    """
    Whether a submission may update the shadow model.

    Both gates, deliberately restated here rather than assumed from the
    caller: P2.7c trust AND P2.8b conceptual evaluability. Onboarding's
    synthetic rows carry `adaptive_eligible=False`, so they are excluded by
    the first gate without needing a special case.
    """
    return (
        submission.adaptive_eligible
        and submission.status in LEARNER_EVIDENCE_STATUSES
        and submission.question_id is not None
    )


def _periods_since(last, now):
    if last is None:
        return 0.0
    elapsed_days = (now - last).total_seconds() / 86400.0
    return max(0.0, elapsed_days / glicko.RATING_PERIOD_DAYS)


def get_or_create_learner_skill(user, topic):
    skill, _ = LearnerTopicSkill.objects.get_or_create(
        user=user, topic=topic,
        defaults={
            "rating": glicko.DEFAULT_RATING,
            "rating_deviation": glicko.DEFAULT_RD,
            "volatility": glicko.DEFAULT_VOLATILITY,
        },
    )
    return skill


def get_or_create_question_skill(question):
    """
    A question's shadow row, seeded from `base_difficulty` as a PRIOR.

    Maximum RD deliberately: `base_difficulty` is a three-valued CSV label that
    no outcome has ever moved, so "somebody typed this once" is exactly what
    RD = 350 encodes. `prior_rating` keeps the seed so a later audit can tell a
    learned rating from an untouched one.
    """
    skill, _ = QuestionSkill.objects.get_or_create(
        question=question,
        defaults={
            "rating": question.base_difficulty,
            "prior_rating": question.base_difficulty,
            "rating_deviation": glicko.DEFAULT_RD,
            "volatility": glicko.DEFAULT_VOLATILITY,
        },
    )
    return skill


def apply_submission(submission, now=None):
    """
    One two-sided Glicko-2 update from a graded submission.

    Returns (learner_skill, question_skill) or None when the submission is not
    evidence. Both sides move: that is what makes question difficulty LEARNED
    rather than asserted, and it is the property the production Elo lacks.
    """
    if not is_shadow_evidence(submission):
        return None

    now = now or timezone.now()
    question = submission.question
    topic = question.topic

    learner = get_or_create_learner_skill(submission.user, topic)
    q_skill = get_or_create_question_skill(question)

    score = 1.0 if submission.status == "accepted" else 0.0

    # Snapshot both sides BEFORE either moves. Updating the learner first and
    # then feeding the learner's NEW rating into the question's update would
    # count the same evidence twice, in the same direction.
    l_rating, l_rd, l_vol = (learner.rating, learner.rating_deviation,
                             learner.volatility)
    q_rating, q_rd, q_vol = (q_skill.rating, q_skill.rating_deviation,
                             q_skill.volatility)

    # Computed once and reused, because these exact values are BOTH fed to
    # `rate` and recorded in the snapshot. Calling `_periods_since` a second
    # time for the snapshot would re-read the clock and could record a
    # different number than the arithmetic used.
    l_periods = _periods_since(learner.last_evidence_at, now)
    q_periods = _periods_since(q_skill.last_evidence_at, now)

    new_l = glicko.rate(
        l_rating, l_rd, l_vol, [(q_rating, q_rd, score)],
        periods_inactive=l_periods)
    new_q = glicko.rate(
        q_rating, q_rd, q_vol, [(l_rating, l_rd, 1.0 - score)],
        periods_inactive=q_periods)

    learner.rating, learner.rating_deviation, learner.volatility = new_l
    learner.evidence_count += 1
    learner.last_evidence_at = now
    learner.save(update_fields=["rating", "rating_deviation", "volatility",
                                "evidence_count", "last_evidence_at",
                                "updated_at"])

    q_skill.rating, q_skill.rating_deviation, q_skill.volatility = new_q
    q_skill.evidence_count += 1
    q_skill.last_evidence_at = now
    q_skill.save(update_fields=["rating", "rating_deviation", "volatility",
                                "evidence_count", "last_evidence_at",
                                "updated_at"])

    # ── Point-in-time snapshot (M2 P2.9b) ────────────────────────────────
    #
    # From the LOCALS captured above, never re-read from the rows — those have
    # just been overwritten, so a re-read would return the AFTER state and
    # every snapshot would silently encode its own outcome.
    #
    # Inside the caller's atomic block (`record_submission_safely`), so a
    # snapshot cannot survive a rolled-back rating update or vice versa: the
    # history either matches the ratings or neither exists.
    #
    # Costs one INSERT and one existence check. No extra SELECT for the state
    # itself, because the update already had to read it.
    glicko_history.record_snapshot(
        submission=submission,
        topic=topic,
        before=((l_rating, l_rd, l_vol), (q_rating, q_rd, q_vol)),
        after=((new_l[0], new_l[1]), (new_q[0], new_q[1])),
        periods=(l_periods, q_periods),
        recorded_at=now,
    )

    return learner, q_skill


def record_submission_safely(submission, now=None):
    """
    The production hook.

    Wrapped in its own savepoint with a catch-all: a shadow failure must roll
    back only shadow writes and must never surface to the learner or abort the
    submission. Shadow mode that can break production is not shadow mode.
    """
    try:
        with transaction.atomic():
            return apply_submission(submission, now=now)
    except Exception:
        logger.exception(
            "shadow model update failed for submission=%s (production "
            "unaffected)", getattr(submission, "pk", None))
        return None


def current_ability(user, topic, now=None):
    """
    (rating, rd) for a learner right now, with inactivity already applied.

    Read-only: the inflation is computed for the caller, not persisted, so
    merely looking at a learner does not age them.
    """
    now = now or timezone.now()
    skill = LearnerTopicSkill.objects.filter(user=user, topic=topic).first()
    if skill is None:
        return glicko.DEFAULT_RATING, glicko.DEFAULT_RD, 0
    rd = glicko.inflate_rd(
        skill.rating_deviation, skill.volatility,
        _periods_since(skill.last_evidence_at, now))
    return skill.rating, rd, skill.evidence_count


def select_question(user, topic, candidates, seed=None, now=None):
    """
    What the shadow model WOULD have chosen. Returns (question, observation).

    `candidates` is supplied by the caller — normally the same queryset
    production used — so the two systems are compared on identical candidate
    sets and any difference is attributable to the scoring, not the filter.

    ── Thompson sampling ───────────────────────────────────────────────────

        theta_sample ~ Normal(theta, RD^2)

    Sampling from the ability posterior, then choosing the question closest to
    the sample, is exploration that is *proportional to ignorance*: a learner
    with RD 350 gets wide exploration, one with RD 50 gets almost pure
    exploitation, and nothing needs an epsilon parameter. Production's
    deterministic argmin cannot do this because it has no posterior to sample
    from.

    `seed` makes a run reproducible, which is required for the comparison
    report to mean anything.
    """
    now = now or timezone.now()
    seed = seed if seed is not None else 0
    rng = random.Random(seed)

    rating, rd, evidence = current_ability(user, topic, now=now)
    sampled = rng.gauss(rating, rd)

    candidate_list = list(candidates)
    if not candidate_list:
        return None, ShadowObservation(
            timestamp=now.isoformat(), user_id=user.pk, topic=topic.name,
            learner_rating=round(rating, 2), learner_rd=round(rd, 2),
            learner_evidence_count=evidence, sampled_ability=round(sampled, 2),
            production_question_id=None, shadow_question_id=None,
            shadow_question_rating=None, shadow_question_rd=None,
            agree=True, difficulty_distance=None, predicted_success=None,
            candidate_count=0, seed=seed, reason="no candidates")

    skills = {
        s.question_id: s for s in
        QuestionSkill.objects.filter(question__in=candidate_list)
    }

    def rating_of(question):
        skill = skills.get(question.pk)
        if skill is None:
            # No shadow row yet: the prior, at maximum uncertainty.
            return question.base_difficulty, glicko.DEFAULT_RD
        return skill.rating, skill.rating_deviation

    best, best_key = None, None
    for question in candidate_list:
        q_rating, q_rd = rating_of(question)
        # Distance from the SAMPLED ability, then question id for a total
        # order — the same determinism requirement P2.8a established.
        key = (abs(q_rating - sampled), question.pk)
        if best_key is None or key < best_key:
            best, best_key = question, key

    q_rating, q_rd = rating_of(best)
    return best, ShadowObservation(
        timestamp=now.isoformat(), user_id=user.pk, topic=topic.name,
        learner_rating=round(rating, 2), learner_rd=round(rd, 2),
        learner_evidence_count=evidence, sampled_ability=round(sampled, 2),
        production_question_id=None, shadow_question_id=best.pk,
        shadow_question_rating=round(q_rating, 2),
        shadow_question_rd=round(q_rd, 2),
        agree=False, difficulty_distance=round(abs(q_rating - sampled), 2),
        predicted_success=round(
            glicko.win_probability(rating, rd, q_rating, q_rd), 4),
        candidate_count=len(candidate_list), seed=seed,
        reason="nearest question rating to the sampled ability")
