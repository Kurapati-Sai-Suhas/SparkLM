"""
Deterministic subsampling of a raw KT source file (M2 P2.12).

P2.12 §22I: prove the pipeline on a documented subset before spending the
phase waiting for a full run. This module makes the subset a REAL FILE with
its own sha256, so a subset result is reproducible in exactly the same way a
full-corpus result is — the build hashes whatever file it is handed, and a
subset that only existed as a flag would be invisible in that hash.

── Whole learners, never whole rows ────────────────────────────────────────

Sampling rows would cut sequences in half. Knowledge tracing predicts the
next response from the earlier ones; a learner whose history is missing a
random 40% is not a smaller sample of the same population, it is a different
and easier problem — the model is asked to bridge gaps it was never told
exist. So the unit of sampling is the LEARNER, and a sampled learner keeps
their whole history in the original row order.

── Deterministic, and not by sorting ───────────────────────────────────────

Learner ids are collected, sorted for a stable base order, then shuffled
under an explicit seed. Taking the first K of the SORTED ids would be
reproducible too, but ASSISTments ids are assigned roughly in enrolment
order and correlate with class and school, so the first K learners are one
cohort rather than a sample. The seed is recorded in the summary.

Pure standard library: no framework, no database, no network.
"""

import csv
import hashlib
import pathlib
import random

#: Raw-file column naming the learner, for each supported source.
LEARNER_COLUMN = {
    "assistments-2009-2010-skill-builder": "user_id",
}


def _sha256(path, chunk_size=1 << 20):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def distinct_learners(source, *, learner_column, encoding="latin-1"):
    """Every learner id in the file, sorted. One streaming pass."""
    seen = set()
    with pathlib.Path(source).open("r", encoding=encoding, newline="") as fh:
        reader = csv.DictReader(fh)
        if learner_column not in (reader.fieldnames or []):
            raise ValueError(
                f"{pathlib.Path(source).name} has no {learner_column!r} "
                f"column; found {reader.fieldnames}")
        for row in reader:
            value = (row.get(learner_column) or "").strip()
            if value:
                seen.add(value)
    return sorted(seen)


def subsample_by_learner(source, destination, *, learners, seed,
                         learner_column, encoding="latin-1"):
    """
    Write a subset containing every row of `learners` sampled learners.

    Returns a summary dict — including the destination's sha256 — that is
    meant to be recorded alongside any result computed from the subset.
    Requesting more learners than the file holds keeps all of them rather
    than raising: a subset larger than the corpus is the corpus, and the
    summary says so.
    """
    source = pathlib.Path(source)
    destination = pathlib.Path(destination)

    everyone = distinct_learners(source, learner_column=learner_column,
                                 encoding=encoding)
    chosen_count = min(int(learners), len(everyone))

    ordered = list(everyone)
    random.Random(seed).shuffle(ordered)
    keep = set(ordered[:chosen_count])

    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with source.open("r", encoding=encoding, newline="") as reader_handle:
        reader = csv.DictReader(reader_handle)
        fieldnames = list(reader.fieldnames or [])
        with destination.open("w", encoding=encoding, newline="") as out_handle:
            writer = csv.DictWriter(out_handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                if (row.get(learner_column) or "").strip() in keep:
                    writer.writerow(row)
                    written += 1

    return {
        "source_file": source.name,
        "source_sha256": _sha256(source),
        "subset_file": destination.name,
        "subset_sha256": _sha256(destination),
        "learner_column": learner_column,
        "seed": seed,
        "learners_requested": int(learners),
        "learners_in_source": len(everyone),
        "learners_kept": chosen_count,
        "rows_kept": written,
        "sampling_unit": "learner (whole history retained, original row order)",
        "is_whole_corpus": chosen_count == len(everyone),
    }
