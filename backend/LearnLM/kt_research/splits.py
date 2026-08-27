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

from dataclasses import dataclass, field

BY_TIME = "time"
BY_LEARNER = "learner"

#: Each learner's own history cut by fraction: the earliest part trains, the
#: latest tests (M2 P2.12).
#:
#: The third strategy, and the one a production KT model is actually asked to
#: do: given THIS learner's history so far, what happens next. `BY_TIME` asks
#: a different question (a global clock), and on a corpus with no shared
#: wall-clock — ASSISTments 2009 has none — a global cut is not even
#: well defined across learners.
#:
#: Learners too short to cut go entirely to TRAIN. Evaluating on a learner
#: with no history measures the cold-start prior, which is a separate
#: experiment with a separate claim.
BY_LEARNER_HISTORY = "learner_history"

SPLIT_STRATEGIES = (BY_TIME, BY_LEARNER, BY_LEARNER_HISTORY)

#: Below this, a learner cannot yield a train, a validation AND a test row.
#: Mirrors `kt_dataset.pipeline.MIN_SPLITTABLE_LENGTH`, which owns the rule for
#: built corpora; this constant governs only corpora that arrive unpartitioned.
MIN_SPLITTABLE_LENGTH = 3


class LeakageError(Exception):
    """A split that would let the model see the future. Never a warning."""


@dataclass(frozen=True)
class Split:
    train: list
    test: list
    strategy: str
    boundary: object
    #: Optional third bucket. Defaulted so every two-way caller and every
    #: existing test constructs a Split exactly as before.
    validation: list = field(default_factory=list)

    def summary(self):
        return {"strategy": self.strategy, "boundary": self.boundary,
                "train": len(self.train), "validation": len(self.validation),
                "test": len(self.test)}


def split(interactions, *, split_by, fraction=0.8, validation_fraction=None,
          time_key="timestamp", learner_key="learner_id"):
    """
    A reproducible split. `split_by` is keyword-only and has NO default.

    `interactions` is a sequence of dicts. Ordering is made total by
    (timestamp, learner_id, index) so that ties cannot reorder between runs
    — a tie broken by dict iteration order is a result nobody can reproduce.

    `validation_fraction` is supported by `BY_LEARNER_HISTORY` only. The other
    two strategies raise rather than quietly returning an empty validation
    bucket, because an experiment that thinks it tuned on a validation set and
    did not is worse off than one that never asked for it.
    """
    if split_by not in SPLIT_STRATEGIES:
        raise ValueError(
            f"split_by must be one of {SPLIT_STRATEGIES}; there is no default "
            f"because the choice changes what the experiment measures")
    if not 0.0 < fraction < 1.0:
        raise ValueError("fraction must be strictly between 0 and 1")
    if validation_fraction is not None:
        if split_by != BY_LEARNER_HISTORY:
            raise ValueError(
                f"validation_fraction is only defined for "
                f"{BY_LEARNER_HISTORY!r}; {split_by!r} would silently return "
                f"an empty validation bucket")
        if not 0.0 < validation_fraction < 1.0:
            raise ValueError("validation_fraction must be in (0, 1)")
        if fraction + validation_fraction >= 1.0:
            raise ValueError(
                "train + validation fractions must leave room for a test set")

    rows = list(interactions)
    if not rows:
        return Split([], [], split_by, None)

    if split_by == BY_LEARNER_HISTORY:
        return _split_learner_history(rows, fraction, validation_fraction,
                                      time_key, learner_key)

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


def _split_learner_history(rows, fraction, validation_fraction, time_key,
                           learner_key):
    """Cut each learner's own sequence. Two-way when no validation asked for."""
    by_learner = {}
    for index, row in enumerate(rows):
        by_learner.setdefault(row[learner_key], []).append((index, row))

    train, validation_rows, test = [], [], []
    for learner in sorted(by_learner):
        sequence = [row for _index, row in
                    sorted(by_learner[learner],
                           key=lambda pair: (pair[1][time_key], pair[0]))]

        if len(sequence) < MIN_SPLITTABLE_LENGTH:
            train.extend(sequence)
            continue

        if validation_fraction is None:
            cut = max(1, min(int(len(sequence) * fraction), len(sequence) - 1))
            train.extend(sequence[:cut])
            test.extend(sequence[cut:])
            continue

        train_end = int(len(sequence) * fraction)
        validation_end = int(len(sequence) * (fraction + validation_fraction))
        # Floor both cuts, then force each bucket non-empty. A short sequence
        # gives its scarce interactions to the LATER buckets: a learner who
        # contributes only to train is invisible to every reported metric.
        train_end = max(1, min(train_end, len(sequence) - 2))
        validation_end = max(train_end + 1,
                             min(validation_end, len(sequence) - 1))
        train.extend(sequence[:train_end])
        validation_rows.extend(sequence[train_end:validation_end])
        test.extend(sequence[validation_end:])

    return Split(train, test, BY_LEARNER_HISTORY,
                 {"train_fraction": fraction,
                  "validation_fraction": validation_fraction},
                 validation=validation_rows)


def assert_no_leakage(split_result, *, time_key="timestamp",
                      learner_key="learner_id"):
    """
    Raise unless the split is sound. Called by the runner before training,
    so an unsound experiment cannot produce a number at all.
    """
    train, test = split_result.train, split_result.test

    if split_result.strategy == BY_LEARNER_HISTORY:
        _assert_learner_history_sound(split_result, time_key, learner_key)
        return

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


def _assert_learner_history_sound(split_result, time_key, learner_key):
    """
    Per learner: every training interaction strictly precedes every validation
    interaction, which strictly precedes every test interaction.

    Checked PER LEARNER, because the split is per learner. A global check
    would flag learner A's late training rows against learner B's early test
    rows and call it leakage, which it is not — with no shared clock their
    positions are not comparable, and a guard that cries wolf gets disabled.

    The same interaction appearing in two buckets is checked separately and
    globally: that one is never legitimate.
    """
    buckets = (("train", split_result.train),
               ("validation", split_result.validation),
               ("test", split_result.test))

    seen = {}
    for name, rows in buckets:
        for row in rows:
            key = (row[learner_key], row[time_key])
            if key in seen and seen[key] != name:
                raise LeakageError(
                    f"learner {row[learner_key]} position {row[time_key]} "
                    f"appears in both {seen[key]} and {name}; one interaction "
                    f"cannot be both trained on and scored")
            seen[key] = name

    ranges = {}
    for name, rows in buckets:
        for row in rows:
            learner = row[learner_key]
            low, high = ranges.setdefault((learner, name),
                                          (row[time_key], row[time_key]))
            ranges[(learner, name)] = (min(low, row[time_key]),
                                       max(high, row[time_key]))

    order = ("train", "validation", "test")
    learners = {learner for learner, _name in ranges}
    for learner in sorted(learners):
        present = [name for name in order if (learner, name) in ranges]
        for earlier, later in zip(present, present[1:]):
            if ranges[(learner, earlier)][1] >= ranges[(learner, later)][0]:
                raise LeakageError(
                    f"learner {learner}: {earlier} ends at position "
                    f"{ranges[(learner, earlier)][1]} but {later} begins at "
                    f"{ranges[(learner, later)][0]}. Every {later} interaction "
                    f"must be strictly later than every {earlier} one.")


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
