"""
Deterministic dataset build (M2 P2.10b).

    raw rows -> validate -> dedupe -> assemble -> order-check -> split -> hash

Same input plus same configuration yields the same processed rows, the same
ordering, the same id mappings, the same split and the same processed hash.
Nothing time-dependent enters the hash — not the build clock, not a run id —
because a hash that changes every run identifies nothing.

── The split is ordinal ───────────────────────────────────────────────────

P2.10a's `temporal_split` takes wall-clock boundaries. ASSISTments 2009 has no
wall-clock time (see `schema`), so this module splits on **per-learner
sequence fraction** instead, and then hands the result to P2.10a's
`audit_split` for verification via an order-preserving embedding of position
into synthetic datetimes.

That embedding is a checking device, not data: the synthetic datetimes exist
only inside the audit call and are never written to a row. The alternative —
inventing timestamps and storing them — is exactly the fabrication this phase
is meant to avoid.

**Per-learner fractional split, not a global cut point.** A global cut on a
dataset with no shared clock would put whole learners on one side (their
`order_id`s are not comparable across learners in any meaningful unit), which
tests generalisation to new LEARNERS rather than to a learner's FUTURE. The
production question is the second one: given this learner's history so far,
what happens next. So each learner's own sequence is cut by fraction, the
earliest part training and the latest testing.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from kt_dataset import validation
from kt_dataset.schema import (
    CANONICAL_COLUMNS, SCHEMA_VERSION, Interaction, Provenance,
)

#: Minimum interactions before a learner can be split at all.
#:
#: With fewer than 3, a train/validation/test cut gives at least one empty
#: bucket for that learner. Such learners are routed entirely to TRAIN: they
#: are real training signal, and evaluating on a learner with no history is
#: measuring the cold-start prior, which is a separate experiment.
MIN_SPLITTABLE_LENGTH = 3

#: Fixed epoch for the order-preserving embedding used by the split audit.
#: Constant, so it never enters the hash as a varying value.
_AUDIT_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


@dataclass
class BuildConfig:
    dataset_name: str
    dataset_version: str
    source_file: str = ""
    require_concept: bool = False
    train_fraction: float = 0.7
    validation_fraction: float = 0.15
    min_learner_length: int = 1
    seed: int = 20260813

    def __post_init__(self):
        if not 0 < self.train_fraction < 1:
            raise ValueError("train_fraction must be in (0, 1)")
        if not 0 < self.validation_fraction < 1:
            raise ValueError("validation_fraction must be in (0, 1)")
        if self.train_fraction + self.validation_fraction >= 1.0:
            raise ValueError(
                "train + validation fractions must leave room for a test split")

    def as_dict(self):
        return asdict(self)


@dataclass
class BuildResult:
    interactions: list = field(default_factory=list)
    train: list = field(default_factory=list)
    validation: list = field(default_factory=list)
    test: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    config: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    raw_dataset_hash: str = ""
    processed_hash: str = ""
    split_hash: str = ""

    @property
    def rejection_counts(self):
        from collections import Counter
        return dict(Counter(r.reason for r in self.rejected))


# ═════════════════════════════════════════════════════════════
# Assembly
# ═════════════════════════════════════════════════════════════

def assemble(rows, config, capabilities, raw_hash):
    """
    (interactions, rejections) — validated, deduped, canonicalised, ordered.

    `attempt_number` is computed here as PRIOR attempts on this
    (learner, question), never read from the source's own `attempt_count`.
    ASSISTments' column counts attempts WITHIN one problem log, which is a
    different quantity, and using it would leak how many tries the learner
    would ultimately need.
    """
    provenance = Provenance(
        dataset_name=config.dataset_name,
        dataset_version=config.dataset_version,
        source_file=config.source_file,
        raw_dataset_hash=raw_hash,
        schema_version=SCHEMA_VERSION,
    )

    materialised = list(rows)
    rejections = []

    valid = []
    for row in materialised:
        problems = validation.validate_raw_row(
            row, require_concept=config.require_concept)
        if problems:
            rejections.extend(problems)
            continue
        valid.append(row)

    valid, duplicate_rejections = validation.drop_source_duplicates(valid)
    rejections.extend(duplicate_rejections)

    # Sort by (learner, order_key) so sequence position is well defined. The
    # source row id breaks ties, keeping the order total and reproducible.
    valid.sort(key=lambda r: (str(r["learner_id"]),
                              int(float(r["order_key"])),
                              str(r.get("source_row_id", ""))))

    interactions, prior = [], {}
    for row in valid:
        learner = str(row["learner_id"])
        question = str(row["question_id"])
        key = (learner, question)
        interactions.append(Interaction(
            learner_id=learner,
            question_id=question,
            concept_id=str(row.get("concept_id", "") or ""),
            sequence_position=int(float(row["order_key"])),
            correct=int(float(row["correct"])),
            attempt_number=prior.get(key, 0),
            source_row_id=str(row.get("source_row_id", "")),
            occurred_at=row.get("occurred_at") if
            capabilities.has_wall_clock_time else None,
            lag_seconds=None,
            outcome_label=str(row.get("outcome_label", "")),
            provenance=provenance,
        ))
        prior[key] = prior.get(key, 0) + 1

    order_problems = validation.assert_monotonic_sequences(interactions)
    rejections.extend(order_problems)

    if config.min_learner_length > 1:
        counts = {}
        for interaction in interactions:
            counts[interaction.learner_id] = counts.get(
                interaction.learner_id, 0) + 1
        interactions = [i for i in interactions
                        if counts[i.learner_id] >= config.min_learner_length]

    return interactions, rejections


# ═════════════════════════════════════════════════════════════
# Split
# ═════════════════════════════════════════════════════════════

def split_by_learner(interactions, config):
    """
    (train, validation, test), cut within each learner's own chronology.

    Boundary rules, stated because they are the cases that silently go wrong:

      * **Equal positions** cannot occur — `assert_monotonic_sequences` rejects
        them before this runs, so no tie-break is needed or invented.
      * **Learners shorter than MIN_SPLITTABLE_LENGTH** go entirely to TRAIN.
        A 1-interaction learner has no future to predict.
      * **Rounding** floors the train and validation cuts, so short sequences
        give their scarce interactions to the later splits rather than
        producing an empty test set for that learner.
    """
    by_learner = {}
    for interaction in interactions:
        by_learner.setdefault(interaction.learner_id, []).append(interaction)

    train, validation_rows, test = [], [], []
    for learner in sorted(by_learner):
        sequence = sorted(by_learner[learner],
                          key=lambda i: i.sequence_position)

        if len(sequence) < MIN_SPLITTABLE_LENGTH:
            train.extend(sequence)
            continue

        train_end = int(len(sequence) * config.train_fraction)
        validation_end = int(
            len(sequence) * (config.train_fraction + config.validation_fraction))

        # Guarantee non-empty validation and test for splittable learners.
        train_end = max(1, min(train_end, len(sequence) - 2))
        validation_end = max(train_end + 1,
                             min(validation_end, len(sequence) - 1))

        train.extend(sequence[:train_end])
        validation_rows.extend(sequence[train_end:validation_end])
        test.extend(sequence[validation_end:])

    return train, validation_rows, test


def audit_split_ordinally(train, validation_rows, test):
    """
    Verify the split with P2.10a's `audit_split`, per learner.

    P2.10a checks wall-clock boundaries; this dataset has only ordinals. Each
    interaction is embedded as `epoch + position seconds` — strictly
    order-preserving, so any ordering violation in position is an ordering
    violation in the embedded time and vice versa. The embedding lives inside
    this function and is never persisted.

    Audited **per learner**, because the split is per learner: a global audit
    would flag learner A's late training rows against learner B's early test
    rows, which is not leakage — their positions are not comparable.
    """
    from groups.kt_leakage import Interaction as LeakInteraction, audit_split

    def embed(rows):
        return [LeakInteraction(
            learner_id=i.learner_id, question_id=i.question_id,
            topic_id=i.concept_id or "0",
            submitted_at=_AUDIT_EPOCH + timedelta(seconds=i.sequence_position),
            outcome=i.correct, attempt_number=i.attempt_number,
            lag_seconds=None) for i in rows]

    # Grouped ONCE per bucket. Re-scanning all three lists per learner is
    # O(learners x interactions), which is 1.7 billion comparisons on the full
    # ASSISTments 2009 file — the audit, not the model, becomes the reason the
    # benchmark cannot be run. Same partition, same comparisons, same result.
    def group(rows):
        grouped = {}
        for interaction in rows:
            grouped.setdefault(interaction.learner_id, []).append(interaction)
        return grouped

    grouped_train = group(train)
    grouped_validation = group(validation_rows)
    grouped_test = group(test)
    learners = (set(grouped_train) | set(grouped_validation)
                | set(grouped_test))

    problems, checks = [], []
    for learner in sorted(learners):
        subset_train = grouped_train.get(learner, [])
        subset_validation = grouped_validation.get(learner, [])
        subset_test = grouped_test.get(learner, [])

        # A learner routed entirely to TRAIN (too short to split) has no
        # held-out rows by design; auditing them would fail on "empty test".
        if not subset_test and not subset_validation:
            continue

        report = audit_split(embed(subset_train), embed(subset_validation),
                             embed(subset_test))
        checks = report.checks_run
        problems.extend(f"learner {learner}: {p}" for p in report.problems)

    from groups.kt_leakage import LeakageReport
    return LeakageReport(problems=problems, checks_run=list(checks))


# ═════════════════════════════════════════════════════════════
# Hashing
# ═════════════════════════════════════════════════════════════

def _frame(label, value):
    label_bytes = str(label).encode("utf-8")
    value_bytes = str(value).encode("utf-8")
    return (b"%d:%s|%d:%s\n"
            % (len(label_bytes), label_bytes, len(value_bytes), value_bytes))


def processed_hash(interactions, config):
    """
    sha256 over the canonical rows plus the configuration.

    Length-prefixed framing, the same construction P2.7g-3 uses for the
    grading-artifact digest: no field value can forge a field boundary. The
    configuration participates because two builds producing identical rows
    under different `require_concept` settings are not the same dataset — one
    of them merely happened to have no missing concepts.
    """
    digest = hashlib.sha256()
    digest.update(_frame("schema_version", SCHEMA_VERSION))
    for key in sorted(config.as_dict()):
        digest.update(_frame(f"config:{key}", config.as_dict()[key]))
    for interaction in interactions:
        row = interaction.as_row()
        for column in CANONICAL_COLUMNS:
            digest.update(_frame(column, row[column]))
    return digest.hexdigest()


def split_hash(train, validation_rows, test):
    """Digest of the partition itself, so a re-split is detectable."""
    digest = hashlib.sha256()
    for name, rows in (("train", train), ("validation", validation_rows),
                       ("test", test)):
        digest.update(_frame("split", name))
        digest.update(_frame("count", len(rows)))
        for interaction in rows:
            digest.update(_frame(
                "row",
                f"{interaction.learner_id}|{interaction.question_id}|"
                f"{interaction.sequence_position}"))
    return digest.hexdigest()


#: Column order for the split assignment sidecar.
SPLIT_COLUMNS = ("source_row_id", "learner_id", "sequence_position", "split")


def split_assignment(result):
    """
    Which bucket each interaction landed in, keyed by `source_row_id`.

    A SIDECAR, deliberately not a column on `interactions.csv`: the canonical
    columns and the processed hash are a published contract, and a partition is
    a property of a build configuration rather than of an interaction. Adding a
    column would change the processed hash of every dataset ever built from
    this schema, which would make old and new builds of identical data look
    like different data.

    It exists so a downstream consumer can USE this split rather than recompute
    it. A second implementation of the split rule is a second chance to get it
    wrong, and the whole point of the partition being hashed is that exactly
    one of them is authoritative.
    """
    for name, rows in (("train", result.train),
                       ("validation", result.validation),
                       ("test", result.test)):
        for interaction in rows:
            yield {"source_row_id": interaction.source_row_id,
                   "learner_id": interaction.learner_id,
                   "sequence_position": interaction.sequence_position,
                   "split": name}


# ═════════════════════════════════════════════════════════════
# Orchestration
# ═════════════════════════════════════════════════════════════

def build(rows, config, capabilities, raw_hash=""):
    """The whole pipeline. Pure: reads nothing, writes nothing."""
    interactions, rejected = assemble(rows, config, capabilities, raw_hash)
    train, validation_rows, test = split_by_learner(interactions, config)

    return BuildResult(
        interactions=interactions,
        train=train, validation=validation_rows, test=test,
        rejected=rejected,
        config=config.as_dict(),
        capabilities=capabilities.as_dict(),
        raw_dataset_hash=raw_hash,
        processed_hash=processed_hash(interactions, config),
        split_hash=split_hash(train, validation_rows, test),
    )


def manifest(result, statistics, leakage):
    """The reproducibility record written beside the processed data."""
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_name": result.config.get("dataset_name"),
        "dataset_version": result.config.get("dataset_version"),
        "source_file": result.config.get("source_file"),
        "raw_dataset_hash": result.raw_dataset_hash,
        "processed_hash": result.processed_hash,
        "split_hash": result.split_hash,
        "configuration": result.config,
        "capabilities": result.capabilities,
        "counts": {
            "interactions": len(result.interactions),
            "train": len(result.train),
            "validation": len(result.validation),
            "test": len(result.test),
            "rejected": len(result.rejected),
        },
        "rejection_counts": result.rejection_counts,
        "statistics": statistics,
        "leakage": leakage.as_dict(),
        "canonical_columns": list(CANONICAL_COLUMNS),
    }


def write_manifest(path, payload):
    import pathlib
    pathlib.Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8")
