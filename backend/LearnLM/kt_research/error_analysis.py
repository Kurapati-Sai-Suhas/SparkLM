"""
Where two models disagree, and on what (M2 P2.13 §23G).

An aggregate AUC says one model is better; it never says where, and a
difference of 0.002 can be a real effect on a small slice or noise spread
evenly. This module splits the held-out interactions into the four
agreement buckets and describes each one by properties that were knowable
BEFORE the learner answered.

── The rule this module must not break ─────────────────────────────────────

§23G: do not use test labels to modify the model. Nothing here trains, tunes
or selects anything — it reads two finished checkpoints and describes their
disagreements. Every stratifying feature is either knowable at prediction
time or computed from the TRAINING split alone, so the analysis could have
been produced without seeing a single test label.

`question_accuracy` is the one that would be easy to get wrong: item
difficulty estimated over the whole corpus would be built from the very
labels being analysed. It is computed from training rows only, and items
never seen in training are reported as a separate stratum rather than given
a corpus-wide average.
"""

import math
from collections import defaultdict

BOTH_RIGHT = "both_right"
BOTH_WRONG = "both_wrong"
REFERENCE_ONLY = "reference_only"
CANDIDATE_ONLY = "candidate_only"
BUCKETS = (BOTH_RIGHT, BOTH_WRONG, REFERENCE_ONLY, CANDIDATE_ONLY)


def question_accuracy(train_rows):
    """
    Empirical success rate per question, from TRAINING rows only.

    A difficulty proxy for a corpus with no difficulty column. Computed over
    the whole corpus it would carry the test labels this module is
    describing, which would make every "harder items" claim circular.
    """
    totals = defaultdict(lambda: [0, 0])
    for row in train_rows:
        entry = totals[row["question_id"]]
        entry[0] += int(bool(row["correct"]))
        entry[1] += 1
    return {question: (right / seen, seen)
            for question, (right, seen) in totals.items()}


def describe_rows(tasks, reference, candidate, train_rows, threshold=0.5):
    """
    One record per scored interaction, with its agreement bucket.

    `reference` and `candidate` are lists of prediction lists aligned with
    `tasks`, exactly as `experiment.score` collects them.
    """
    difficulty = question_accuracy(train_rows)
    records = []

    for (sequence, indices), reference_scores, candidate_scores in zip(
            tasks, reference, candidate):
        seen_concepts = defaultdict(int)
        durations = []
        for position, row in enumerate(sequence):
            concept = row.get("concept_id", "")
            repetitions = seen_concepts[concept]
            seen_concepts[concept] += 1

            previous = sequence[position - 1] if position else None
            previous_duration = (previous.get("response_time_ms")
                                 if previous else None)
            durations.append(previous_duration)

            if position not in indices:
                continue

            label = bool(row["correct"])
            reference_right = (reference_scores[position] >= threshold) == label
            candidate_right = (candidate_scores[position] >= threshold) == label

            if reference_right and candidate_right:
                bucket = BOTH_RIGHT
            elif reference_right:
                bucket = REFERENCE_ONLY
            elif candidate_right:
                bucket = CANDIDATE_ONLY
            else:
                bucket = BOTH_WRONG

            accuracy, seen = difficulty.get(row["question_id"], (None, 0))
            records.append({
                "learner_id": row["learner_id"],
                "concept_id": concept,
                "correct": label,
                "bucket": bucket,
                "history_length": position,
                "concept_repetitions": repetitions,
                "attempt_number": int(row.get("attempt_number", 0) or 0),
                "previous_duration_s": (None if previous_duration is None
                                        else previous_duration / 1000.0),
                "question_train_accuracy": accuracy,
                "question_train_count": seen,
                "reference_p": reference_scores[position],
                "candidate_p": candidate_scores[position],
            })
    return records


# ═════════════════════════════════════════════════════════════
# Strata
# ═════════════════════════════════════════════════════════════

def _bucket_of(value, edges):
    if value is None:
        return "unknown"
    for low, high, label in edges:
        if low <= value < high:
            return label
    return edges[-1][2]


HISTORY_BINS = [(0, 1, "first interaction"), (1, 5, "2-5"), (5, 20, "6-20"),
                (20, 50, "21-50"), (50, math.inf, "50+")]
REPETITION_BINS = [(0, 1, "concept unseen"), (1, 3, "seen 1-2"),
                   (3, 10, "seen 3-9"), (10, math.inf, "seen 10+")]
DURATION_BINS = [(0, 5, "under 5s"), (5, 20, "5-20s"), (20, 60, "20-60s"),
                 (60, 600, "1-10min"), (600, math.inf, "over 10min")]
DIFFICULTY_BINS = [(0.0, 0.4, "hard (<40% in train)"),
                   (0.4, 0.7, "medium (40-70%)"),
                   (0.7, 1.01, "easy (>70%)")]

STRATA = {
    "history_length": ("history_length", HISTORY_BINS),
    "concept_repetitions": ("concept_repetitions", REPETITION_BINS),
    "previous_duration": ("previous_duration_s", DURATION_BINS),
    "question_difficulty": ("question_train_accuracy", DIFFICULTY_BINS),
}


def stratify(records, stratum):
    """
    Bucket counts for one stratum, plus the net swing toward the candidate.

    `net` is (candidate-only wins) minus (reference-only wins). It is
    reported as a COUNT and as a share of the stratum, because a slice
    holding 200 interactions can show a large share and mean nothing.
    """
    field, edges = STRATA[stratum]
    grouped = defaultdict(lambda: dict.fromkeys(BUCKETS, 0))

    for record in records:
        grouped[_bucket_of(record[field], edges)][record["bucket"]] += 1

    labels = [label for _low, _high, label in edges] + ["unknown"]
    rows = []
    for label in labels:
        counts = grouped.get(label)
        if not counts:
            continue
        total = sum(counts.values())
        net = counts[CANDIDATE_ONLY] - counts[REFERENCE_ONLY]
        rows.append({
            "stratum": label,
            "interactions": total,
            **counts,
            "net_candidate": net,
            "net_share": round(net / total, 5) if total else 0.0,
            "disagreement_share": round(
                (counts[REFERENCE_ONLY] + counts[CANDIDATE_ONLY]) / total, 5)
            if total else 0.0,
        })
    return rows


def summarise(records, reference_name, candidate_name):
    counts = dict.fromkeys(BUCKETS, 0)
    for record in records:
        counts[record["bucket"]] += 1
    total = len(records)

    return {
        "reference": reference_name,
        "candidate": candidate_name,
        "scored_interactions": total,
        "agreement": {
            **counts,
            "agree_share": round(
                (counts[BOTH_RIGHT] + counts[BOTH_WRONG]) / total, 5)
            if total else 0.0,
            "net_candidate": counts[CANDIDATE_ONLY] - counts[REFERENCE_ONLY],
        },
        "strata": {name: stratify(records, name) for name in STRATA},
    }


def render(summary):
    """
    A readable report. Counts and net swing, never a story.

    ASCII only: this prints to a Windows console whose default code page is
    cp1252, and a box-drawing character there is an unhandled exception
    rather than a cosmetic problem.
    """
    lines = [
        f"ERROR ANALYSIS  {summary['reference']} (reference) vs "
        f"{summary['candidate']} (candidate)",
        f"  scored            {summary['scored_interactions']:,}",
    ]
    agreement = summary["agreement"]
    lines += [
        f"  both right        {agreement[BOTH_RIGHT]:,}",
        f"  both wrong        {agreement[BOTH_WRONG]:,}",
        f"  reference only    {agreement[REFERENCE_ONLY]:,}",
        f"  candidate only    {agreement[CANDIDATE_ONLY]:,}",
        f"  they agree on     {agreement['agree_share']:.1%} of interactions",
        f"  net to candidate  {agreement['net_candidate']:+,}",
        "",
    ]

    for name, rows in summary["strata"].items():
        lines.append(f"  -- {name.replace('_', ' ')} " + "-" * (46 - len(name)))
        lines.append(f"    {'stratum':<24}{'n':>9}{'ref only':>10}"
                     f"{'cand only':>11}{'net':>8}{'net %':>8}")
        for row in rows:
            lines.append(
                f"    {row['stratum']:<24}{row['interactions']:>9,}"
                f"{row[REFERENCE_ONLY]:>10,}{row[CANDIDATE_ONLY]:>11,}"
                f"{row['net_candidate']:>+8,}{row['net_share']:>8.1%}")
        lines.append("")
    return "\n".join(lines)
