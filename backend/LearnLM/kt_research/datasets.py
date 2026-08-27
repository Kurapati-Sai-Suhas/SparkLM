"""
Loading a built corpus into the research pipeline (M2 P2.12).

This package does not read raw benchmark files and does not map them. The
`kt_dataset` package already owns that: it validates rows, applies the
duplicate policy, computes `attempt_number`, orders each learner's history,
partitions it and hashes the result. Re-implementing any of that here would
give two answers to questions that must have one.

The boundary between them is a DIRECTORY, not an import:

    manage.py kt_dataset_build ...  ->  interactions.csv
                                        split_assignment.csv
                                        manifest.json
                                              |
                                              v
                                  kt_research.datasets.load_build()

Everything downstream of that directory is pure standard library and torch:
no web framework, no application models, no database, no network. That is
what makes the isolation in P2.12 §22H structural rather than a promise.

── The row contract ────────────────────────────────────────────────────────

    learner_id    str    who
    question_id   str    which item
    concept_id    str    which skill ("" when the source has none)
    correct       int    1 / 0 — the label
    timestamp     int    THE ORDERING KEY, not a wall clock (see below)

`timestamp` is the name the splitter and the causality guard use for
"whatever orders this sequence". For ASSISTments 2009 it carries
`sequence_position`, an ORDINAL derived from `order_id`. The corpus has no
absolute time column at all, and none is invented here — `kt_dataset.schema`
records that finding in full.

── Fields P2.12 §22B asks for that this corpus cannot supply ───────────────

Documented rather than fabricated, and each with the actual reason:

  response_time   The raw file HAS it (`ms_first_response`), but canonical
                  schema v1 does not carry it, so it does not survive the
                  build. Adding it is a schema change that would invalidate
                  the processed hash of every corpus ever built from v1.
                  No baseline in this phase consumes it, and §22E forbids
                  temporal features, so the change is deferred rather than
                  smuggled in.

  difficulty      Not a column in the source. It could be ESTIMATED as each
                  item's empirical success rate — and that would be a
                  leak: computed over the whole corpus it carries test-set
                  labels into a training feature, and computing it over
                  train alone leaves every test-only item undefined. AKT
                  learns Rasch difficulty as a parameter instead of being
                  handed one, which is the honest way to use it.
"""

import csv
import json
import pathlib
from dataclasses import dataclass, field

from kt_research import splits

#: The keys every row in this package carries. See the module docstring.
ROW_FIELDS = ("learner_id", "question_id", "concept_id", "correct",
              "timestamp")

#: `interactions.csv` column that becomes `timestamp`.
ORDERING_COLUMN = "sequence_position"

AVAILABLE = "available"
UNAVAILABLE = "unavailable"

#: Present as an ORDER but not as the quantity §22B names.
#:
#: A third status because the other two both mislead here. "Available" would
#: let a later phase reach for elapsed time that does not exist; "unavailable"
#: would suggest the corpus cannot be sequenced at all, and it can — that is
#: the whole basis of the split.
ORDINAL_ONLY = "ordinal-only"

STATUSES = (AVAILABLE, ORDINAL_ONLY, UNAVAILABLE)

#: Every field P2.12 §22B names, and what actually became of it.
FIELD_AVAILABILITY = {
    "learner_id": (AVAILABLE, "source column `user_id`."),
    "question_id": (AVAILABLE, "source column `problem_id`."),
    "concept_id": (
        AVAILABLE,
        "source column `skill_id`. ~16% of raw rows have none; whether those "
        "are kept is the build's `require_concept` setting and is recorded in "
        "the manifest either way."),
    "correctness": (AVAILABLE, "source column `correct`, validated as a "
                               "strict 0/1 label — a partial score is "
                               "rejected, never rounded."),
    "timestamp": (
        ORDINAL_ONLY,
        "No absolute time column exists in this corpus; "
        "`ms_first_response` and `overlap_time` are durations, not points in "
        "time. `sequence_position` (from `order_id`) orders each learner's "
        "history and nothing here treats it as a clock."),
    "response_time": (
        UNAVAILABLE,
        "Present in the raw file as `ms_first_response`, absent from "
        "canonical schema v1, so it does not reach this pipeline. No baseline "
        "in this phase consumes it."),
    "difficulty": (
        UNAVAILABLE,
        "Not a source column. Estimating it from corpus-wide success rate "
        "would carry test labels into a training feature."),
}


@dataclass
class LoadedDataset:
    """A built corpus plus the partition the build committed to."""

    name: str
    version: str
    rows: list
    partition: object
    manifest: dict = field(default_factory=dict)
    directory: str = ""

    @property
    def learners(self):
        return {row["learner_id"] for row in self.rows}

    @property
    def questions(self):
        return {row["question_id"] for row in self.rows}

    @property
    def concepts(self):
        return {row["concept_id"] for row in self.rows if row["concept_id"]}

    def describe(self):
        correct = sum(row["correct"] for row in self.rows)
        return {
            "dataset": self.name,
            "version": self.version,
            "interactions": len(self.rows),
            "learners": len(self.learners),
            "questions": len(self.questions),
            "concepts": len(self.concepts),
            "correct_rate": (round(correct / len(self.rows), 6)
                             if self.rows else None),
            "raw_dataset_hash": self.manifest.get("raw_dataset_hash", ""),
            "processed_hash": self.manifest.get("processed_hash", ""),
            "split_hash": self.manifest.get("split_hash", ""),
            "split": self.partition.summary(),
            "field_availability": {
                name: {"status": status, "reason": reason}
                for name, (status, reason) in FIELD_AVAILABILITY.items()},
        }


class BuildNotUsable(Exception):
    """The directory is not a complete corpus build. Never worked around."""


def load_build(directory):
    """
    Read a `kt_dataset_build` output directory.

    The partition is READ, not recomputed. `kt_research` then re-verifies it
    with its own leakage guard, which is a different thing from re-deriving
    it: an independent check has an independent failure mode, whereas a
    second implementation of the split rule just agrees with itself.
    """
    directory = pathlib.Path(directory)
    interactions_path = directory / "interactions.csv"
    assignment_path = directory / "split_assignment.csv"
    manifest_path = directory / "manifest.json"

    for path in (interactions_path, assignment_path, manifest_path):
        if not path.is_file():
            raise BuildNotUsable(
                f"{path.name} is missing from {directory}. Build the corpus "
                f"first:\n"
                f"  manage.py kt_dataset_build --source <name> --input <file> "
                f"--out {directory}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    rows, by_row_id = [], {}
    with interactions_path.open("r", encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            row = {
                "learner_id": record["learner_id"],
                "question_id": record["question_id"],
                "concept_id": record.get("concept_id", "") or "",
                "correct": int(float(record["correct"])),
                "timestamp": int(float(record[ORDERING_COLUMN])),
            }
            rows.append(row)
            by_row_id[record["source_row_id"]] = row

    buckets = {"train": [], "validation": [], "test": []}
    unmatched = 0
    with assignment_path.open("r", encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            row = by_row_id.get(record["source_row_id"])
            if row is None:
                unmatched += 1
                continue
            buckets[record["split"]].append(row)

    if unmatched:
        raise BuildNotUsable(
            f"{unmatched} split assignments name a row that is not in "
            f"interactions.csv. The two files are from different builds, and "
            f"a partition from one corpus applied to another is not a split.")

    assigned = sum(len(v) for v in buckets.values())
    if assigned != len(rows):
        raise BuildNotUsable(
            f"{len(rows)} interactions but {assigned} split assignments. "
            f"An unassigned interaction would be silently dropped from every "
            f"reported metric.")

    partition = splits.Split(
        train=buckets["train"], test=buckets["test"],
        strategy=splits.BY_LEARNER_HISTORY,
        boundary={"source": "kt_dataset build",
                  "split_hash": manifest.get("split_hash", "")},
        validation=buckets["validation"])

    return LoadedDataset(
        name=manifest.get("dataset_name", "unknown"),
        version=manifest.get("dataset_version", "unknown"),
        rows=rows, partition=partition, manifest=manifest,
        directory=str(directory))
