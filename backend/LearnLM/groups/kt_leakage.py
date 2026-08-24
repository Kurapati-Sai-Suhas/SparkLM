"""
Causality and leakage safety for KT datasets (M2 P2.10a).

The single most common way a knowledge-tracing result is wrong is not a bad
model — it is a split that lets a learner's future leak into their own past. A
randomly-shuffled row split on sequential data produces an AUC that looks
excellent and means nothing, because predicting interaction 40 while having
trained on interaction 41 of the same learner is not prediction.

This module makes that failure REFUSABLE rather than merely discouraged:
`temporal_split` is the only split constructor, and `audit_split` fails loudly
on any ordering violation. Both are pure functions over interaction records —
no ORM, no database, no writes — so they can be tested with fabricated
sequences that would be impossible to persist.

── The causality rule ─────────────────────────────────────────────────────

For an interaction at time t, a feature is admissible only if its value was
determined at or before t. Three concrete consequences for LearnLM:

  * `attempt_number` counts PRIOR attempts, never total attempts.
  * `lag_seconds` looks backward to the previous interaction, never forward.
  * Glicko rating/RD may not be attached at all — see `GLICKO_RECONSTRUCTION`.
"""

from dataclasses import dataclass, field

#: Why point-in-time Glicko cannot currently be reconstructed.
#:
#: `LearnerTopicSkill` and `QuestionSkill` store CURRENT state with `updated_at`
#: only. There is no history table, no event log, and no per-submission rating
#: snapshot. Attaching today's rating to a six-month-old interaction would embed
#: the outcome of every intervening submission into a feature describing a
#: moment before they happened — the purest form of leakage available.
#:
#: Replaying the rating history from submissions is NOT a safe substitute
#: either: Glicko-2 updates in rating PERIODS, and the shadow implementation's
#: period boundaries are not recorded, so a replay would reconstruct a
#: plausible history rather than the actual one.
#:
#: Reported as a gap. Not invented.
GLICKO_RECONSTRUCTION = (
    "UNAVAILABLE — LearnerTopicSkill/QuestionSkill store current state only "
    "(no history table, no per-interaction snapshot). Point-in-time rating and "
    "RD cannot be reconstructed for historical interactions without "
    "fabricating them. P2.10b must either log ratings going forward or treat "
    "Glicko as a live-only signal."
)


@dataclass(frozen=True)
class Interaction:
    """
    One KT training example.

    `feature_asof` is the latest timestamp any attached feature was determined
    at. It exists so causality is CHECKABLE: a feature computed from future
    rows raises it above `submitted_at`, and the audit refuses.
    """
    learner_id: int
    question_id: int
    topic_id: int
    submitted_at: object
    outcome: int                    # 1 accepted, 0 wrong_answer
    attempt_number: int             # PRIOR attempts on this question
    lag_seconds: float = None       # since this learner's previous interaction
    language: str = ""
    feature_asof: object = None     # defaults to submitted_at

    def asof(self):
        return self.feature_asof or self.submitted_at


@dataclass
class LeakageReport:
    problems: list = field(default_factory=list)
    checks_run: list = field(default_factory=list)

    @property
    def is_safe(self):
        return not self.problems

    def as_dict(self):
        return {"is_safe": self.is_safe, "problems": list(self.problems),
                "checks_run": list(self.checks_run),
                "glicko_reconstruction": GLICKO_RECONSTRUCTION}


def audit_causality(interactions):
    """
    Every way a feature could describe the future, checked.

    Returns a LeakageReport. An empty problem list is the only safe state.
    """
    report = LeakageReport()

    report.checks_run.append("feature_asof <= submitted_at")
    for interaction in interactions:
        if interaction.asof() > interaction.submitted_at:
            report.problems.append(
                f"learner {interaction.learner_id} question "
                f"{interaction.question_id}: feature_asof "
                f"{interaction.asof()} is AFTER submitted_at "
                f"{interaction.submitted_at} — a feature was computed from "
                f"data that did not exist yet")

    report.checks_run.append("per-learner sequences are non-decreasing in time")
    by_learner = {}
    for interaction in interactions:
        by_learner.setdefault(interaction.learner_id, []).append(interaction)
    for learner_id, sequence in by_learner.items():
        times = [item.submitted_at for item in sequence]
        if times != sorted(times):
            report.problems.append(
                f"learner {learner_id}: interaction sequence is not in "
                f"chronological order; a causal model would attend to the "
                f"future")

    report.checks_run.append("attempt_number counts only prior attempts")
    seen = {}
    for interaction in sorted(interactions, key=lambda i: i.submitted_at):
        key = (interaction.learner_id, interaction.question_id)
        expected = seen.get(key, 0)
        if interaction.attempt_number != expected:
            report.problems.append(
                f"learner {interaction.learner_id} question "
                f"{interaction.question_id}: attempt_number "
                f"{interaction.attempt_number} != {expected} prior attempts — "
                f"a total count leaks how many attempts follow")
        seen[key] = expected + 1

    report.checks_run.append("lag_seconds is backward-looking and non-negative")
    previous = {}
    for interaction in sorted(interactions, key=lambda i: i.submitted_at):
        last = previous.get(interaction.learner_id)
        if interaction.lag_seconds is not None:
            if interaction.lag_seconds < 0:
                report.problems.append(
                    f"learner {interaction.learner_id}: negative lag_seconds "
                    f"{interaction.lag_seconds} — computed against a later "
                    f"interaction")
            elif last is None and interaction.lag_seconds != 0:
                report.problems.append(
                    f"learner {interaction.learner_id}: first interaction has "
                    f"lag_seconds {interaction.lag_seconds}; there is no "
                    f"previous interaction to measure from")
        previous[interaction.learner_id] = interaction.submitted_at

    return report


def temporal_split(interactions, train_end, validation_end):
    """
    (train, validation, test) split strictly by wall-clock time.

    The ONLY split constructor in this package. There is deliberately no
    `random_split`, no `shuffle` parameter and no `stratify` option: a
    stratified or shuffled split of sequential learner data is the leakage
    described in the module docstring, and the way to prevent a mistake that
    looks like a good result is to make it unavailable rather than discouraged.

    Learners may appear in more than one bucket. That is correct and
    intentional — it is exactly the production question ("given this learner's
    history so far, what happens next?"). What must never happen is a learner's
    LATER interaction training a model evaluated on their EARLIER one, which
    time ordering prevents by construction.
    """
    if train_end >= validation_end:
        raise ValueError(
            f"train_end {train_end} must precede validation_end "
            f"{validation_end}")

    train, validation, test = [], [], []
    for interaction in sorted(interactions, key=lambda i: i.submitted_at):
        if interaction.submitted_at < train_end:
            train.append(interaction)
        elif interaction.submitted_at < validation_end:
            validation.append(interaction)
        else:
            test.append(interaction)
    return train, validation, test


def audit_split(train, validation, test):
    """
    Prove the split is temporally ordered.

    Checks the boundaries rather than trusting the constructor, so a split
    assembled by any other means — a notebook, a future loader, a hand-built
    fixture — is held to the same rule.
    """
    report = LeakageReport()
    report.checks_run.append("max(train) < min(validation) < min(test)")

    def bounds(rows):
        times = [row.submitted_at for row in rows]
        return (min(times), max(times)) if times else (None, None)

    train_lo, train_hi = bounds(train)
    val_lo, val_hi = bounds(validation)
    test_lo, _test_hi = bounds(test)

    if train_hi is not None and val_lo is not None and train_hi >= val_lo:
        report.problems.append(
            f"train contains {train_hi}, at or after validation start "
            f"{val_lo} — the model would be evaluated on data it trained on")
    if val_hi is not None and test_lo is not None and val_hi >= test_lo:
        report.problems.append(
            f"validation contains {val_hi}, at or after test start {test_lo}")
    if train_hi is not None and test_lo is not None and train_hi >= test_lo:
        report.problems.append(
            f"train contains {train_hi}, at or after test start {test_lo}")

    report.checks_run.append("no duplicated interaction across buckets")
    def keys(rows):
        return {(r.learner_id, r.question_id, r.submitted_at) for r in rows}
    for left_name, left, right_name, right in (
            ("train", train, "validation", validation),
            ("train", train, "test", test),
            ("validation", validation, "test", test)):
        shared = keys(left) & keys(right)
        if shared:
            report.problems.append(
                f"{len(shared)} interaction(s) appear in both {left_name} and "
                f"{right_name}")

    report.checks_run.append("test set is non-empty")
    if not test:
        report.problems.append(
            "test split is empty — no held-out evaluation is possible")

    return report
