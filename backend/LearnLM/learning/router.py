"""
learning.router — Traffic-Cop routing math, v2 (frozen architecture §6.2).

The v1 router fed np.var of the binary outcome window to its models and
heuristic. For binary outcomes the variance is p(1-p) — fully determined
by the mean — so the feature carried zero independent information and
mechanically forced every user in the 0.60–0.72 accuracy band to flat
practice regardless of their actual pattern (at p in that band,
p(1-p) > 0.20, the old threshold). v2 measures what variance pretended
to: pattern, via the Wald–Wolfowitz runs test.

Sign convention: positive z = more runs than chance = oscillating
pass/fail (erratic — the signal the router treats as "rebuild
confidence in flat practice"); negative z = fewer runs = streaky
(e.g. a failure block followed by a clean run — a breakthrough, not
erratic, and must not be routed flat when accuracy is adequate).

Everything in this module is pure: outcomes arrive as sequences, no ORM.
"""

import math

# Serve-time and train-time must build the identical vector — this
# constant plus feature_rows() is the single definition both sides use
# (§5 feature contracts; the v1 train/serve skew is the cautionary
# precedent: retrain_ai used topic-mastery aggregates while the router
# served window statistics).
FEATURES_V2 = ("avg_acc", "runs_z", "avg_elo", "engine_flag")

# Runs-test z above this = oscillation beyond the 95% chance level.
OSCILLATION_Z_THRESHOLD = 1.96
# Below this mean accuracy the user is struggling regardless of pattern.
ACCURACY_THRESHOLD = 0.60
# §6.2: windows shorter than this carry no pattern signal; z is 0.
MIN_RUNS_TEST_N = 5


def runs_test_z(outcomes) -> float:
    """
    Wald–Wolfowitz runs-test z-score over a binary outcome sequence
    (§6.2). Returns 0.0 (pattern-neutral) when the sequence is shorter
    than MIN_RUNS_TEST_N or contains only one outcome class (the test
    statistic is undefined in both cases — no pattern evidence exists).
    """
    seq = [1 if o else 0 for o in outcomes]
    n = len(seq)
    if n < MIN_RUNS_TEST_N:
        return 0.0

    n1 = sum(seq)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return 0.0

    runs = 1 + sum(1 for a, b in zip(seq, seq[1:]) if a != b)

    expected = (2.0 * n1 * n0) / n + 1.0
    variance = (2.0 * n1 * n0 * (2.0 * n1 * n0 - n)) / (n * n * (n - 1.0))
    if variance <= 0.0:
        return 0.0

    return (runs - expected) / math.sqrt(variance)


def outcome_stats(outcomes):
    """
    (mean accuracy, runs-test z, sample size) for a binary outcome
    window — the router's serve-time telemetry triple.
    """
    seq = [1.0 if o else 0.0 for o in outcomes]
    if not seq:
        return 0.0, 0.0, 0
    return sum(seq) / len(seq), runs_test_z(seq), len(seq)


def decide_route(avg_acc: float, runs_z: float) -> str:
    """
    v2 fallback heuristic (used whenever no evaluated classifier is
    loaded): oscillating beyond chance OR low accuracy -> flat Elo
    practice (skill-matched confidence rebuilding); otherwise the user
    advances through the hierarchical prerequisite DAG. Streaky-negative
    z with adequate accuracy deliberately routes hierarchical — the
    exact case the v1 variance statistic got wrong.
    """
    if runs_z > OSCILLATION_Z_THRESHOLD or avg_acc < ACCURACY_THRESHOLD:
        return "flat"
    return "hierarchical"


def feature_rows(avg_acc: float, runs_z: float, avg_elo: float):
    """
    The two candidate FEATURES_V2 rows for one student — engine_flag 0.0
    (flat) and 1.0 (hierarchical) — in contract order. predict_proba over
    both rows lets the classifier route to the higher predicted success.
    """
    return (
        [avg_acc, runs_z, avg_elo, 0.0],
        [avg_acc, runs_z, avg_elo, 1.0],
    )
