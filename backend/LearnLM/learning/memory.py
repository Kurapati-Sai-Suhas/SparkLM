"""
learning.memory — retention and effective-mastery math (frozen §6.2).

Pure functions over passed values; no ORM, no HTTP (§9 rules).

The HLR state (halflife per user-topic) is maintained by the SM-2 update
in ProgressionService; this module answers the read-side questions:
"what fraction of that skill is predicted to still be recallable now?"
and "which practiced topics are due for review?".

Skill is the observed accuracy for now — §6.2 defines the mastery
posterior as *initialized from* the accuracy rule, and the belief layer
(M11) upgrades it in place later.
"""

# Predicted recall below this on a practiced topic ⇒ due for review.
RETENTION_DUE_THRESHOLD = 0.5

# HLR halflife is bounded to [0.1, 100] days on write; mirror the floor
# defensively on the read side.
MIN_HALFLIFE_DAYS = 0.1


def retention(halflife_days: float, days_since_practice: float) -> float:
    """
    Predicted recall probability P(t) = 2^(−t/h) — the HLR forgetting
    curve. Clamped to [0, 1]; non-positive halflives are floored rather
    than allowed to explode the exponent.
    """
    h = max(float(halflife_days), MIN_HALFLIFE_DAYS)
    t = max(float(days_since_practice), 0.0)
    return min(1.0, max(0.0, 2.0 ** (-t / h)))


def effective_mastery(skill: float, retention_now: float) -> float:
    """
    §6.2: effective mastery = skill × predicted retention(t). Both inputs
    clamped to [0, 1]; the product is what curriculum gating will use
    once EFFECTIVE_MASTERY_GATING flips on (staged rollout).
    """
    s = min(1.0, max(0.0, float(skill)))
    r = min(1.0, max(0.0, float(retention_now)))
    return s * r


def is_due(reviews: int, retention_now: float) -> bool:
    """
    Due for review = the user has actually practiced the topic AND the
    forgetting curve predicts recall below threshold. Unpracticed topics
    are never "due" — there is nothing to forget.
    """
    return reviews > 0 and retention_now < RETENTION_DUE_THRESHOLD
