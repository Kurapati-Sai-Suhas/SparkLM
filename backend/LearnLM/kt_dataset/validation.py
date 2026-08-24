"""
Row validation and duplicate policy (M2 P2.10b).

Two rules govern this module.

**Nothing is silently discarded.** Every rejected row produces a
`Rejection` carrying the source row id and a machine-readable reason code, and
the counts appear in the manifest. A pipeline that drops 40% of its input
without saying so produces a dataset whose statistics describe a population
nobody chose.

**A repeated attempt is data, not noise.** The duplicate policy distinguishes
a source-level defect (the same record emitted twice) from a learner genuinely
attempting the same question again. ASSISTments 2009 is documented to contain
the first kind — the reason a corrected release exists — and removing the
second kind would delete exactly the signal knowledge tracing exists to model.
"""

from collections import Counter
from dataclasses import dataclass

# ── Rejection reason codes (machine-readable, stable) ──────────────────────
MISSING_LEARNER = "missing_learner_id"
MISSING_QUESTION = "missing_question_id"
MISSING_CONCEPT = "missing_concept_id"
MISSING_CORRECT = "missing_correct"
MALFORMED_CORRECT = "malformed_correct"
MALFORMED_NUMERIC = "malformed_numeric"
MISSING_ORDER = "missing_order_key"
SOURCE_DUPLICATE = "source_duplicate"
NON_MONOTONIC = "non_monotonic_sequence"

#: Reasons that indicate a defect in the SOURCE FILE rather than a policy
#: choice. Separated so the manifest can report "this dataset is dirty" apart
#: from "we chose to exclude these".
QUALITY_REASONS = frozenset({
    MISSING_LEARNER, MISSING_QUESTION, MISSING_CORRECT, MALFORMED_CORRECT,
    MALFORMED_NUMERIC, MISSING_ORDER, SOURCE_DUPLICATE, NON_MONOTONIC,
})


@dataclass(frozen=True)
class Rejection:
    source_row_id: str
    reason: str
    detail: str = ""

    def as_dict(self):
        return {"source_row_id": self.source_row_id, "reason": self.reason,
                "detail": self.detail}


@dataclass
class ValidationOutcome:
    accepted: list
    rejected: list

    @property
    def rejection_counts(self):
        return dict(Counter(r.reason for r in self.rejected))

    @property
    def quality_rejections(self):
        return sum(1 for r in self.rejected if r.reason in QUALITY_REASONS)

    def as_dict(self):
        return {"accepted": len(self.accepted), "rejected": len(self.rejected),
                "rejection_counts": self.rejection_counts,
                "quality_rejections": self.quality_rejections}


def validate_raw_row(row, *, require_concept):
    """
    Reasons this raw source row cannot become an interaction. Empty = valid.

    `require_concept` is a per-source decision, not a universal rule.
    ASSISTments 2009 has ~16% missing `skill_id`; dropping those rows loses a
    sixth of the dataset, while keeping them means concept-conditioned models
    cannot use them. The choice belongs to the caller and is recorded in the
    manifest configuration either way.
    """
    reasons = []

    if not str(row.get("learner_id", "")).strip():
        reasons.append(Rejection(_row_id(row), MISSING_LEARNER))
    if not str(row.get("question_id", "")).strip():
        reasons.append(Rejection(_row_id(row), MISSING_QUESTION))
    if require_concept and not str(row.get("concept_id", "")).strip():
        reasons.append(Rejection(_row_id(row), MISSING_CONCEPT))

    raw_correct = row.get("correct", None)
    if raw_correct is None or str(raw_correct).strip() == "":
        reasons.append(Rejection(_row_id(row), MISSING_CORRECT))
    else:
        try:
            value = float(str(raw_correct).strip())
        except (TypeError, ValueError):
            reasons.append(Rejection(_row_id(row), MALFORMED_CORRECT,
                                     f"{raw_correct!r} is not numeric"))
        else:
            # Compared as a FLOAT against exactly 0.0/1.0, never via int().
            # `int(float("0.5"))` is 0, so an int-first check silently records
            # a half-credit score as a wrong answer — a partial-credit column
            # would be relabelled instead of rejected, and the label noise
            # would be invisible in every downstream metric. Caught by
            # test_malformed_correctness_is_rejected_not_coerced.
            if value not in (0.0, 1.0):
                reasons.append(Rejection(
                    _row_id(row), MALFORMED_CORRECT,
                    f"{raw_correct!r} is not a binary 0/1 label; a graded or "
                    f"partial score needs an explicit thresholding decision"))

    order = row.get("order_key", None)
    if order is None or str(order).strip() == "":
        reasons.append(Rejection(_row_id(row), MISSING_ORDER))
    else:
        try:
            int(float(str(order).strip()))
        except (TypeError, ValueError):
            reasons.append(Rejection(_row_id(row), MALFORMED_NUMERIC,
                                     f"order_key {order!r} is not numeric"))

    return reasons


def _row_id(row):
    return str(row.get("source_row_id", "") or "")


# ═════════════════════════════════════════════════════════════
# Duplicate policy
# ═════════════════════════════════════════════════════════════

#: A SOURCE DUPLICATE is an exact repeat of the full identity tuple:
#:
#:     (learner_id, question_id, order_key, correct)
#:
#: including the ordering key. Two records claiming to be the same attempt, at
#: the same position, with the same outcome, are the same record emitted twice
#: — the defect ASSISTments 2009's corrected release exists to fix.
#:
#: A LEGITIMATE REPEAT shares (learner_id, question_id) but has a DIFFERENT
#: order_key: the learner came back and tried again. This is kept. Removing it
#: would delete the within-learner repetition that knowledge tracing models,
#: and would flatter every baseline by removing its hardest cases.
DUPLICATE_IDENTITY = ("learner_id", "question_id", "order_key", "correct")


def identity(row):
    return tuple(str(row.get(field, "")) for field in DUPLICATE_IDENTITY)


def drop_source_duplicates(rows):
    """
    (kept, rejected) — first occurrence wins, in the input's given order.

    FIRST occurrence, deterministically, so two runs over the same file keep
    the same physical row. "Last wins" would be equally arbitrary but would
    make the result depend on how far the reader got, which matters if a build
    is ever chunked.
    """
    seen, kept, rejected = set(), [], []
    for row in rows:
        key = identity(row)
        if key in seen:
            rejected.append(Rejection(
                _row_id(row), SOURCE_DUPLICATE,
                f"identical {DUPLICATE_IDENTITY} already seen"))
            continue
        seen.add(key)
        kept.append(row)
    return kept, rejected


def assert_monotonic_sequences(interactions):
    """
    Rejections for any learner whose sequence positions are not increasing.

    Checked AFTER assembly rather than trusted from the source, because
    `sequence_position` is what the split orders by. If it is not monotone the
    split is meaningless, and a source whose ordering key turns out not to be
    ordered (see `schema` on ASSISTments `order_id`) must be caught here rather
    than discovered as an unexplained metric.
    """
    rejections, previous = [], {}
    for interaction in interactions:
        last = previous.get(interaction.learner_id)
        if last is not None and interaction.sequence_position <= last:
            rejections.append(Rejection(
                interaction.source_row_id, NON_MONOTONIC,
                f"learner {interaction.learner_id}: position "
                f"{interaction.sequence_position} follows {last}"))
        previous[interaction.learner_id] = max(
            interaction.sequence_position, last if last is not None else -1)
    return rejections
