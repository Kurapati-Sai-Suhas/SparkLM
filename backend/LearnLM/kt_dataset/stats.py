"""Dataset statistics (M2 P2.10b). Pure; no plotting dependency."""

from collections import Counter


def describe(result):
    """Every count P2.10b's §10 asks for, as plain JSON-safe data."""
    interactions = result.interactions
    if not interactions:
        return {"interactions": 0, "note": "empty dataset — nothing to describe"}

    per_learner = Counter(i.learner_id for i in interactions)
    per_question = Counter(i.question_id for i in interactions)
    per_concept = Counter(i.concept_id or "(none)" for i in interactions)
    lengths = sorted(per_learner.values())
    correct = sum(i.correct for i in interactions)

    return {
        "interactions": len(interactions),
        "learners": len(per_learner),
        "questions": len(per_question),
        "concepts": len([c for c in per_concept if c != "(none)"]),
        "interactions_without_concept": per_concept.get("(none)", 0),

        "correctness": {
            "correct": correct,
            "incorrect": len(interactions) - correct,
            "rate": round(correct / len(interactions), 6),
        },

        "sequence_length": {
            "min": lengths[0],
            "median": _median(lengths),
            "mean": round(sum(lengths) / len(lengths), 4),
            "max": lengths[-1],
            "buckets": _length_buckets(lengths),
        },

        "per_question": {
            "min": min(per_question.values()),
            "median": _median(sorted(per_question.values())),
            "max": max(per_question.values()),
            "seen_once": sum(1 for n in per_question.values() if n == 1),
        },

        "per_concept": {
            "min": min(per_concept.values()),
            "median": _median(sorted(per_concept.values())),
            "max": max(per_concept.values()),
        },

        "cold_start": {
            # The slice a KT model cannot help with, and the reason the
            # deterministic + Glicko fallback exists.
            "learners_lt_5": sum(1 for n in lengths if n < 5),
            "learners_lt_10": sum(1 for n in lengths if n < 10),
            "interactions_in_lt_5_learners": sum(n for n in lengths if n < 5),
            "share_of_interactions_in_lt_5_learners": round(
                sum(n for n in lengths if n < 5) / len(interactions), 6),
        },

        "split": {
            "train": len(result.train),
            "validation": len(result.validation),
            "test": len(result.test),
            "train_correct_rate": _rate(result.train),
            "validation_correct_rate": _rate(result.validation),
            "test_correct_rate": _rate(result.test),
            "learners_train_only": len(
                {i.learner_id for i in result.train}
                - {i.learner_id for i in result.test}),
        },

        "position_range": {
            "min": min(i.sequence_position for i in interactions),
            "max": max(i.sequence_position for i in interactions),
        },
    }


def _rate(rows):
    return round(sum(i.correct for i in rows) / len(rows), 6) if rows else None


def _median(ordered):
    if not ordered:
        return 0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _length_buckets(lengths):
    buckets = {"1-2": 0, "3-5": 0, "6-10": 0, "11-20": 0, "21-50": 0, "50+": 0}
    for length in lengths:
        if length <= 2:
            buckets["1-2"] += 1
        elif length <= 5:
            buckets["3-5"] += 1
        elif length <= 10:
            buckets["6-10"] += 1
        elif length <= 20:
            buckets["11-20"] += 1
        elif length <= 50:
            buckets["21-50"] += 1
        else:
            buckets["50+"] += 1
    return buckets
