"""
Running one experiment reproducibly (M2 P2.11b, extended M2 P2.12).

An experiment that cannot be re-run is an anecdote. This module records
everything needed to reproduce a number: the dataset fingerprint, the split
strategy and boundary, the model and its parameters, the seed, and the
metrics — in one JSON file per run.

The leakage check runs BEFORE training, not after scoring, so an unsound
split cannot produce a number at all.

── Every model scores the same rows, with the same context ─────────────────

A learner's history is not restarted at the test boundary. To score a test
interaction, the model is fed that learner's EARLIER interactions — training
ones included — and then asked about the held-out one. That is the question a
deployed knowledge tracer answers, and it is the only version of the question
all three models can answer identically.

Restarting the sequence at the boundary would have measured something else
entirely: with no history, BKT falls back to its prior and a sequence model
has nothing to attend to, so the table would compare three cold-start priors
and call it knowledge tracing.

Causality is preserved throughout — a scored row's own label is never an
input to its own prediction (see `neural` for how the shift makes that
structural).
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
CHECKPOINTS = pathlib.Path(__file__).resolve().parent / "checkpoints"

#: Paths in a run record are written relative to this.
#:
#: A record is a committed artifact in a public repository. An absolute path
#: pins it to one machine's filesystem layout, publishes that layout, and
#: makes the record unusable to anyone who re-runs the experiment elsewhere.
PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Bumped when preprocessing or the scoring protocol changes, so two records
#: that report different numbers for the same corpus can be told apart.
PREPROCESSING_VERSION = "p2.12/v1"


def portable(path):
    """A path relative to the project root, with forward slashes."""
    try:
        relative = pathlib.Path(path).resolve().relative_to(PACKAGE_ROOT)
    except ValueError:
        return str(path)
    return str(relative).replace("\\", "/")

@dataclass
class ExperimentConfig:
    model: str
    dataset: str
    split_by: str
    fraction: float = 0.8
    validation_fraction: float = None
    seed: int = 20260827
    parameters: dict = field(default_factory=dict)
    notes: str = ""
    dataset_directory: str = ""
    preprocessing_version: str = PREPROCESSING_VERSION
    #: Distinguishes artifacts from runs that differ only by seed. Empty by
    #: default so P2.12's checkpoint and result filenames are unchanged.
    tag: str = ""

    @property
    def slug(self):
        return (f"{self.model}_{self.dataset}"
                + (f"_{self.tag}" if self.tag else ""))


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


def sequences_by_learner(interactions, learner_key="learner_id",
                         time_key="timestamp"):
    grouped = {}
    for row in interactions:
        grouped.setdefault(row[learner_key], []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda r: r[time_key])
    return grouped


def build_tasks(context_rows, scored_rows, learner_key="learner_id",
                time_key="timestamp"):
    """
    [(sequence, scored_indices), ...] for the shared scoring path.

    `context_rows` is every interaction that may appear in the sequence — the
    scored ones and everything before them, never anything after. `scored_rows`
    names which positions the metric is computed over.

    Identity is (learner, ordering key). Object identity would work today
    because the loader hands out the same dicts, but a metric that silently
    scores nothing the moment a caller copies a row is not a metric anyone
    should rely on.
    """
    wanted = {(row[learner_key], row[time_key]) for row in scored_rows}
    tasks = []
    for learner, sequence in sorted(
            sequences_by_learner(context_rows, learner_key, time_key).items()):
        indices = [position for position, row in enumerate(sequence)
                   if (learner, row[time_key]) in wanted]
        if indices:
            tasks.append((sequence, indices))
    return tasks


def score(model, tasks):
    """
    (labels, scores) — one path, for every model and every bucket.

    One sequence at a time, deliberately: see the note in `neural` on why
    batching this was measured, found to be worth 1.2x, and rejected for
    moving the numbers in the seventh decimal.
    """
    labels, scores = [], []
    for sequence, indices in tasks:
        predictions = model.predict_sequence(sequence)
        for position in indices:
            labels.append(bool(sequence[position]["correct"]))
            scores.append(predictions[position])
    return labels, scores


def metrics_for(labels, scores):
    """The three P2.12 §22D asks for, plus RMSE, which P2.11b already used."""
    return {
        "auc": model_zoo.auc(labels, scores),
        "accuracy": model_zoo.accuracy(labels, scores),
        "log_loss": model_zoo.log_loss(labels, scores),
        "rmse": model_zoo.rmse(labels, scores),
        "test_interactions": len(labels),
        "positive_rate": (round(sum(1 for l in labels if l) / len(labels), 6)
                          if labels else None),
    }


def run(config, interactions, *, save=True, partition=None,
        dataset_description=None, checkpoint=None):
    """
    Split, check for leakage, fit, score, record.

    `partition` is an already-computed split — the one a corpus build
    committed to and hashed. When supplied it is USED and re-verified, never
    recomputed: a second implementation of a split rule agrees with itself,
    which is not the same as being right.

    `checkpoint` defaults to whatever `save` is. A run that is not being
    recorded is not an artifact, and it should not leave weights behind in
    the working tree — which is exactly what the test suite was doing until
    this default was tied to `save`.
    """
    random.seed(config.seed)
    if checkpoint is None:
        checkpoint = save

    fingerprint = dataset_fingerprint(interactions)

    if partition is None:
        split_result = splits.split(
            interactions, split_by=config.split_by, fraction=config.fraction,
            validation_fraction=config.validation_fraction)
    else:
        split_result = partition

    # BEFORE training. An unsound split must not be able to produce a number.
    splits.assert_no_leakage(split_result)
    train_sequences = sequences_by_learner(split_result.train)
    splits.assert_within_sequence_causality(train_sequences)

    validation_rows = list(split_result.validation)
    validation_tasks = build_tasks(
        list(split_result.train) + validation_rows, validation_rows) \
        if validation_rows else []

    test_tasks = build_tasks(
        list(split_result.train) + validation_rows + list(split_result.test),
        split_result.test)

    model = model_zoo.build(config.model, **config.parameters)
    model.fit(train_sequences, validation=validation_tasks or None)

    test_metrics = metrics_for(*score(model, test_tasks))
    validation_metrics = (metrics_for(*score(model, validation_tasks))
                          if validation_tasks else None)

    checkpoint_path = ""
    if checkpoint and hasattr(model, "save"):
        CHECKPOINTS.mkdir(parents=True, exist_ok=True)
        suffix = getattr(model, "checkpoint_suffix", ".pt")
        checkpoint_path = portable(model.save(
            CHECKPOINTS / f"{config.slug}{suffix}"))

    record = {
        "config": asdict(config),
        "preprocessing_version": config.preprocessing_version,
        "dataset_fingerprint": fingerprint,
        "dataset": dataset_description or {},
        "split": split_result.summary(),
        "metrics": test_metrics,
        "validation_metrics": validation_metrics,
        "training_history": getattr(model, "history", []),
        # What this rung switched on, what it cost, and how long it took —
        # §23E asks for the last two, and the first is what makes a row in
        # the ablation table mean anything.
        "components": getattr(model, "components", {}),
        "parameters": getattr(model, "parameter_count", 0),
        "training_seconds": getattr(model, "training_seconds", None),
        "mean_gate": (model.mean_gate(list(train_sequences.values()))
                      if hasattr(model, "mean_gate") else None),
        "checkpoint": checkpoint_path,
        "environment": {"python": sys.version.split()[0],
                        "platform": platform.platform()},
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    if save:
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / f"{config.slug}_{config.split_by}.json").write_text(
            json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return record


def aggregate(records):
    """
    Mean and sample standard deviation per model across seeds (§23F).

    SAMPLE standard deviation (n-1). With three seeds the population form
    understates the spread by about 18%, and understating the spread is
    exactly how a difference smaller than the noise gets reported as a
    result.
    """
    import statistics
    from collections import defaultdict

    grouped = defaultdict(list)
    for record in records:
        grouped[record["config"]["model"]].append(record)

    summary = {}
    for model, runs in grouped.items():
        entry = {"seeds": sorted(r["config"]["seed"] for r in runs),
                 "runs": len(runs),
                 "parameters": runs[0]["parameters"],
                 "components": runs[0]["components"]}
        for metric in ("auc", "accuracy", "log_loss", "rmse"):
            values = [r["metrics"][metric] for r in runs]
            entry[metric] = {
                "mean": statistics.fmean(values),
                "std": (statistics.stdev(values) if len(values) > 1 else 0.0),
                "values": values,
            }
        times = [r["training_seconds"] for r in runs
                 if r.get("training_seconds")]
        entry["training_seconds_mean"] = (statistics.fmean(times)
                                          if times else None)
        # The MINIMUM is the honest estimate of what this rung costs. Wall
        # clock on a shared machine measures whatever else was running, and
        # a run that happened to overlap something else can triple. The
        # fastest run is the one least contaminated by contention; the mean
        # is kept beside it so a large gap is visible rather than smoothed.
        entry["training_seconds_min"] = min(times) if times else None
        entry["training_seconds_all"] = times
        gates = [r["mean_gate"] for r in runs if r.get("mean_gate") is not None]
        entry["mean_gate"] = statistics.fmean(gates) if gates else None
        summary[model] = entry
    return summary


def ablation_table(summary, order=None):
    """The §23E/§23F table. Only models that actually ran appear."""
    names = [n for n in (order or sorted(summary)) if n in summary]
    lines = ["| Model | AUC | Accuracy | Log Loss | Params | Train (s) |",
             "| --- | --- | --- | --- | --- | --- |"]
    for name in names:
        entry = summary[name]
        fastest = entry.get("training_seconds_min")
        time_text = "n/a" if fastest is None else f"{fastest:,.0f}"
        lines.append(
            f"| {name} "
            f"| {entry['auc']['mean']:.4f} ± {entry['auc']['std']:.4f} "
            f"| {entry['accuracy']['mean']:.4f} ± {entry['accuracy']['std']:.4f} "
            f"| {entry['log_loss']['mean']:.4f} ± {entry['log_loss']['std']:.4f} "
            f"| {entry['parameters']:,} "
            f"| {time_text} |")
    return "\n".join(lines)


def comparison_table(records):
    """
    The §22F table, from whatever records exist.

    Renders only models that actually ran. A row for a model that was never
    trained, or a blank cell, invites the reader to fill it in from
    expectation — which is exactly how a result nobody measured ends up being
    cited.
    """
    header = ("| Model | AUC | Accuracy | Log Loss | RMSE | Scored |\n"
              "| --- | --- | --- | --- | --- | --- |")
    lines = [header]
    for record in records:
        metrics = record["metrics"]
        lines.append(
            f"| {record['config']['model']} "
            f"| {metrics['auc']:.4f} "
            f"| {metrics['accuracy']:.4f} "
            f"| {metrics['log_loss']:.4f} "
            f"| {metrics['rmse']:.4f} "
            f"| {metrics['test_interactions']:,} |")
    return "\n".join(lines)


def write_metrics_csv(path, records):
    """Flat metrics for the whole comparison, beside the per-run JSON."""
    import csv

    columns = ("model", "dataset", "split_by", "seed", "preprocessing_version",
               "dataset_fingerprint", "auc", "accuracy", "log_loss", "rmse",
               "test_interactions", "positive_rate", "checkpoint")
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for record in records:
            metrics = record["metrics"]
            writer.writerow({
                "model": record["config"]["model"],
                "dataset": record["config"]["dataset"],
                "split_by": record["config"]["split_by"],
                "seed": record["config"]["seed"],
                "preprocessing_version": record["preprocessing_version"],
                "dataset_fingerprint": record["dataset_fingerprint"],
                "auc": metrics["auc"],
                "accuracy": metrics["accuracy"],
                "log_loss": metrics["log_loss"],
                "rmse": metrics["rmse"],
                "test_interactions": metrics["test_interactions"],
                "positive_rate": metrics["positive_rate"],
                "checkpoint": record["checkpoint"],
            })
    return str(path)
