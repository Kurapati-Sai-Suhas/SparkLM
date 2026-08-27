"""
Running one experiment reproducibly (M2 P2.11b).

An experiment that cannot be re-run is an anecdote. This module records
everything needed to reproduce a number: the dataset fingerprint, the split
strategy and boundary, the model and its parameters, the seed, and the
metrics — in one JSON file per run.

The leakage check runs BEFORE training, not after scoring, so an unsound
split cannot produce a number at all.
"""

import hashlib
import json
import pathlib
import platform
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from kt_research import models as model_zoo
from kt_research import splits

RESULTS = pathlib.Path(__file__).resolve().parent / "results"


@dataclass
class ExperimentConfig:
    model: str
    dataset: str
    split_by: str
    fraction: float = 0.8
    seed: int = 20260827
    parameters: dict = field(default_factory=dict)
    notes: str = ""


def dataset_fingerprint(interactions):
    """
    A digest of the data actually used.

    Two runs reporting different numbers from "the same dataset" is the
    commonest irreproducibility in this field, and it is usually a different
    filter rather than a different model.
    """
    digest = hashlib.sha256()
    for row in interactions:
        digest.update(repr(sorted(row.items())).encode("utf-8"))
    return digest.hexdigest()


def sequences_by_learner(interactions, learner_key="learner_id"):
    grouped = {}
    for row in interactions:
        grouped.setdefault(row[learner_key], []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda r: r["timestamp"])
    return grouped


def run(config, interactions, *, save=True):
    """Split, check for leakage, fit, score, record."""
    random.seed(config.seed)

    fingerprint = dataset_fingerprint(interactions)
    split_result = splits.split(interactions, split_by=config.split_by,
                                fraction=config.fraction)
    # BEFORE training. An unsound split must not be able to produce a number.
    splits.assert_no_leakage(split_result)
    train_sequences = sequences_by_learner(split_result.train)
    splits.assert_within_sequence_causality(train_sequences)

    model = model_zoo.build(config.model, **config.parameters)
    model.fit(train_sequences)

    labels, scores = [], []
    for rows in sequences_by_learner(split_result.test).values():
        predictions = model.predict_sequence(rows)
        for row, score in zip(rows, predictions):
            labels.append(bool(row["correct"]))
            scores.append(score)

    metrics = {"auc": model_zoo.auc(labels, scores),
               "accuracy": model_zoo.accuracy(labels, scores),
               "rmse": model_zoo.rmse(labels, scores),
               "test_interactions": len(labels)}

    record = {
        "config": asdict(config),
        "dataset_fingerprint": fingerprint,
        "split": split_result.summary(),
        "metrics": metrics,
        "environment": {"python": sys.version.split()[0],
                        "platform": platform.platform()},
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    if save:
        RESULTS.mkdir(exist_ok=True)
        name = f"{config.model}_{config.dataset}_{config.split_by}.json"
        (RESULTS / name).write_text(
            json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return record
