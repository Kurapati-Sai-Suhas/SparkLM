"""
Dataset readers (M2 P2.10b).

**No dataset is bundled, downloaded or vendored by this module.** It maps a
file the operator has already obtained under its own licence into the canonical
schema. ASSISTments' terms require a written agreement and prohibit
redistribution, so shipping the bytes — or fetching them automatically — would
breach terms this repository cannot accept on anyone's behalf.

`synthetic` exists so the pipeline can be tested end to end without any real
data. Its output is stamped `dataset_name="synthetic"` at the record level, so
a synthetic build cannot be mistaken for a real one downstream.
"""

import csv
import hashlib
import pathlib
import random

from kt_dataset.schema import SourceCapabilities

# ═════════════════════════════════════════════════════════════
# ASSISTments 2009-2010 skill builder
# ═════════════════════════════════════════════════════════════

ASSISTMENTS_2009 = SourceCapabilities(
    name="assistments-2009-2010-skill-builder",
    version="corrected-one-row-per-student-problem",
    has_wall_clock_time=False,
    has_concepts=True,
    has_attempt_counts=True,
    has_response_time=True,
    has_point_in_time_rating=False,
    notes=(
        "Skill-builder = MASTERY LEARNING data: a sequence terminates when the "
        "learner answers 3 in a row correctly. This is a selection effect, not "
        "a sampling artefact — sequences end on success by construction, which "
        "inflates the positive rate near the end of every sequence and caps "
        "sequence length for strong learners. Any baseline result on this "
        "dataset must be read with that in mind."),
    unavailable_reasons={
        "wall_clock_time":
            "No timestamp column exists. `ms_first_response` and "
            "`overlap_time` are DURATIONS in milliseconds, not points in "
            "time. Ordering is by `order_id` only.",
        "inter_event_interval":
            "Requires wall-clock time. The gap between two of a learner's "
            "`order_id`s counts OTHER logged interactions in between, which "
            "correlates with elapsed time but is not a measure of it — it "
            "also moves with how busy the platform was.",
        "attempt_count_column":
            "`attempt_count` and `hint_count` describe how the learner's "
            "engagement with this problem ENDED — how many tries and hints "
            "they ultimately needed. Reading either at the position it "
            "describes leaks the outcome. `attempt_number` (PRIOR attempts "
            "on this learner+question) is computed instead and is knowable "
            "before the learner answers.",
        "lag_seconds":
            "Requires wall-clock time, which this source does not have. "
            "SAINT+'s lag-time feature therefore cannot be reproduced on this "
            "benchmark at all.",
        "point_in_time_rating":
            "No rating system is present in the source.",
    },
)

#: Columns this reader requires. The full file has ~30; these are the ones the
#: canonical schema needs, named exactly as the published schema names them.
ASSISTMENTS_REQUIRED = ("order_id", "user_id", "problem_id", "correct")
ASSISTMENTS_OPTIONAL = ("skill_id", "skill_name", "attempt_count",
                        "ms_first_response", "original", "hint_count")


def read_assistments_2009(path, *, raw_hash, encoding="latin-1"):
    """
    Yield raw dicts in the pipeline's intermediate shape.

    `latin-1` by default: the published file is not clean UTF-8 and a strict
    decode fails partway through, which would silently truncate the dataset if
    the reader swallowed the error.

    **`order_id` is used as `order_key`, and its chronology is contested.** The
    EduData schema reference describes it as a "non-chronological record
    identifier"; the prevailing convention in the KT literature is to sort by
    it as a chronological proxy. The pipeline therefore treats it as an ORDINAL
    ONLY and never as a timestamp, and `assert_monotonic_sequences` re-checks
    the resulting order rather than trusting it.
    """
    location = pathlib.Path(path)
    with location.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in ASSISTMENTS_REQUIRED
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"{location.name} is missing required ASSISTments columns "
                f"{missing}; found {reader.fieldnames}")

        for line_number, row in enumerate(reader, start=2):
            yield {
                "source_row_id": f"{location.name}:{line_number}",
                "learner_id": (row.get("user_id") or "").strip(),
                "question_id": (row.get("problem_id") or "").strip(),
                "concept_id": (row.get("skill_id") or "").strip(),
                "correct": (row.get("correct") or "").strip(),
                "order_key": (row.get("order_id") or "").strip(),
                "attempt_count_raw": (row.get("attempt_count") or "").strip(),
                "response_time_raw": (
                    row.get("ms_first_response") or "").strip(),
                "outcome_label": "correct" if (
                    row.get("correct") or "").strip() == "1" else "incorrect",
                "occurred_at": None,          # this source has none
                "raw_dataset_hash": raw_hash,
                "source_file": location.name,
            }


# ═════════════════════════════════════════════════════════════
# Synthetic
# ═════════════════════════════════════════════════════════════

SYNTHETIC = SourceCapabilities(
    name="synthetic",
    version="v1",
    has_wall_clock_time=False,
    has_concepts=True,
    has_attempt_counts=True,
    has_response_time=False,
    has_point_in_time_rating=False,
    notes=("Generated from a seeded RNG for pipeline validation. NOT evidence "
           "about any learner population, real or simulated."),
)


def synthetic_rows(*, learners=40, concepts=5, questions=30,
                   max_length=25, seed=20260813):
    """
    A deterministic fake dataset in ASSISTments' intermediate shape.

    Exists so every leakage guarantee, hash guarantee and split boundary can be
    tested without possessing licensed data. A crude 1PL response model gives
    it enough structure that a broken pipeline shows up as a degenerate
    statistic rather than passing on noise.
    """
    rng = random.Random(seed)
    ability = {f"L{n}": rng.gauss(0.0, 1.0) for n in range(learners)}
    difficulty = {f"Q{n}": rng.gauss(0.0, 1.0) for n in range(questions)}
    concept_of = {f"Q{n}": f"C{n % concepts}" for n in range(questions)}

    order, rows = 0, []
    for learner in sorted(ability):
        for _ in range(rng.randint(1, max_length)):
            order += 1
            question = f"Q{rng.randrange(questions)}"
            logit = ability[learner] - difficulty[question]
            probability = 1.0 / (1.0 + pow(2.718281828, -logit))
            correct = 1 if rng.random() < probability else 0
            rows.append({
                "source_row_id": f"synthetic:{order}",
                "learner_id": learner,
                "question_id": question,
                "concept_id": concept_of[question],
                "correct": str(correct),
                "order_key": str(order),
                "attempt_count_raw": "1",
                "outcome_label": "correct" if correct else "incorrect",
                "occurred_at": None,
                "raw_dataset_hash": "synthetic-no-raw-file",
                "source_file": "synthetic",
            })
    return rows


# ═════════════════════════════════════════════════════════════
# Hashing
# ═════════════════════════════════════════════════════════════

def hash_file(path, chunk_size=1 << 20):
    """
    sha256 of a raw source file, streamed.

    Identifies the exact bytes a build consumed. Since the licence forbids
    redistributing the file, the hash is how a result stays checkable by
    someone who obtained their own copy — they can confirm it is the same
    revision without either party sending the data.
    """
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


REGISTRY = {
    ASSISTMENTS_2009.name: (ASSISTMENTS_2009, read_assistments_2009),
    SYNTHETIC.name: (SYNTHETIC, None),
}
