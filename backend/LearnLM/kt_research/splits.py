"""
Temporal splitting, and the leakage guard (M2 P2.11b).

The single most important file in this package. A knowledge-tracing result
is worthless if the model saw the future, and the ways to see the future are
subtle enough that they need to be checked rather than avoided by care.

── Three leaks this guards against ─────────────────────────────────────────

1. **Interaction leakage.** A random split puts a learner's later
   interactions in train and earlier ones in test, so the model predicts a
   past it has already been told about. Splitting BY TIME fixes this.

2. **Learner leakage.** Even a temporal split leaks if the same learner
   appears on both sides and the model can memorise them. Which is correct
   depends on the question being asked, so `split_by` is explicit and has
   no default: "next response for a known learner" and "a learner never
   seen before" are different claims, and a paper that does not say which
   it measured has not measured either.

3. **Boundary leakage.** An interaction exactly ON the cut must fall on one
   side deterministically, or two runs of the same experiment disagree.

`groups/kt_leakage.py` already reasons about this for SparkLM's own data.
This module is for PUBLIC datasets and does not read production.
"""

from dataclasses import dataclass

BY_TIME = "time"
BY_LEARNER = "learner"
SPLIT_STRATEGIES = (BY_TIME, BY_LEARNER)


class LeakageError(Exception):
    """A split that would let the model see the future. Never a warning."""


@dataclass(frozen=True)
class Split:
    train: list
    test: list
    strategy: str
    boundary: object

    def summary(self):
        return {"strategy": self.strategy, "boundary": self.boundary,
                "train": len(self.train), "test": len(self.test)}


def split(interactions, *, split_by, fraction=0.8, time_key="timestamp",
          learner_key="learner_id"):
    """
    A reproducible split. `split_by` is keyword-only and has NO default.

    `interactions` is a sequence of dicts. Ordering is made total by
    (timestamp, learner_id, index) so that ties cannot reorder between runs
    — a tie broken by dict iteration order is a result nobody can reproduce.
    """
    if split_by not in SPLIT_STRATEGIES:
        raise ValueError(
            f"split_by must be one of {SPLIT_STRATEGIES}; there is no default "
            f"because the choice changes what the experiment measures")
    if not 0.0 < fraction < 1.0:
        raise ValueError("fraction must be strictly between 0 and 1")

    rows = list(interactions)
    if not rows:
        return Split([], [], split_by, None)

    if split_by == BY_TIME:
        ordered = sorted(
            enumerate(rows),
            key=lambda pair: (pair[1][time_key],
                              pair[1].get(learner_key, ""), pair[0]))
        ordered = [row for _index, row in ordered]
        cut = int(len(ordered) * fraction)
        boundary = ordered[cut][time_key] if cut < len(ordered) else None
        # Everything strictly before the boundary trains. An interaction ON
        # the boundary goes to test, deterministically.
        train = [r for r in ordered if r[time_key] < boundary] if boundary \
            else ordered
        test = [r for r in ordered if r[time_key] >= boundary] if boundary \
            else []
        return Split(train, test, BY_TIME, boundary)

    learners = sorted({r[learner_key] for r in rows})
    cut = int(len(learners) * fraction)
    held_out = set(learners[cut:])
    train = [r for r in rows if r[learner_key] not in held_out]
    test = [r for r in rows if r[learner_key] in held_out]
    return Split(train, test, BY_LEARNER, sorted(held_out))


def assert_no_leakage(split_result, *, time_key="timestamp",
                      learner_key="learner_id"):
    """
    Raise unless the split is sound. Called by the runner before training,
    so an unsound experiment cannot produce a number at all.
    """
    train, test = split_result.train, split_result.test
    if not train or not test:
        return

    if split_result.strategy == BY_TIME:
        latest_train = max(r[time_key] for r in train)
        earliest_test = min(r[time_key] for r in test)
        if latest_train >= earliest_test:
            raise LeakageError(
                f"temporal split overlaps: train ends at {latest_train} and "
                f"test begins at {earliest_test}. Every test interaction must "
                f"be strictly later than every training interaction.")

    if split_result.strategy == BY_LEARNER:
        shared = ({r[learner_key] for r in train}
                  & {r[learner_key] for r in test})
        if shared:
            raise LeakageError(
                f"{len(shared)} learner(s) appear in both splits; a "
                f"learner-held-out experiment must not let the model memorise "
                f"the learner it is later scored on")


def assert_within_sequence_causality(sequences):
    """
    Within one learner's sequence, position i may only use interactions
    before it.

    A separate check because it catches a different bug: a model that
    accidentally consumes its own label. Given sequences of dicts with
    `timestamp`, this verifies each is non-decreasing — an out-of-order
    sequence means the featureiser has already scrambled causality and no
    masking in the model can recover it.
    """
    for learner, rows in sequences.items():
        stamps = [r["timestamp"] for r in rows]
        if stamps != sorted(stamps):
            raise LeakageError(
                f"learner {learner}: interactions are not in time order, so "
                f"position i can see interactions that happened after it")
