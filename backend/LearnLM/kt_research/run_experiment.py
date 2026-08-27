"""
The experiment entry point (M2 P2.12 §22G).

Four commands, each reproducible from a config file and a seed:

    # 1. a documented subset of a raw benchmark file (optional, see §22I)
    python -m kt_research.run_experiment subsample \\
        --source kt_research/data/raw/skill_builder_data_corrected.csv \\
        --out kt_research/data/raw/assist2009_subset_500.csv \\
        --learners 500 --seed 20260827

    # 2. build the corpus  (this step, and ONLY this step, is run through the
    #    application's management command — it owns the canonical mapping)
    python manage.py kt_dataset_build \\
        --source assistments-2009-2010-skill-builder \\
        --input <raw or subset csv> --out build/kt/assist09 --require-concept

    # 3. train every model in the config
    python -m kt_research.run_experiment train \\
        --config kt_research/configs/assist2009_baselines.json

    # 4. re-score from the saved checkpoints, training nothing
    python -m kt_research.run_experiment evaluate \\
        --config kt_research/configs/assist2009_baselines.json

`evaluate` exists to be the check that `train` is honest. It loads the
checkpoints, rebuilds the same corpus and the same partition, and re-scores.
If its numbers differ from the training run's, something in the pipeline is
not deterministic and every result from it is provisional.

Nothing in this module reaches the application, its models or its database.
The corpus arrives as a directory of files.
"""

import argparse
import json
import pathlib
import sys

from kt_research import datasets, experiment, models, splits, subsample


def load_config(path):
    payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    for required in ("dataset", "dataset_directory", "split_by", "seed",
                     "models"):
        if required not in payload:
            raise SystemExit(f"config is missing {required!r}: {path}")
    return payload


def configs_for(payload):
    """One ExperimentConfig per model named in the config file."""
    for name in payload["models"]:
        parameters = dict(payload["models"][name])
        # The run's seed governs every model. A model carrying its own would
        # make "same seed, same numbers" false in a way nobody would look for.
        parameters.setdefault("seed", payload["seed"])
        yield experiment.ExperimentConfig(
            model=name,
            dataset=payload["dataset"],
            split_by=payload["split_by"],
            seed=payload["seed"],
            parameters=parameters,
            dataset_directory=payload["dataset_directory"],
            notes=payload.get("notes", ""))


def load_corpus(payload):
    loaded = datasets.load_build(payload["dataset_directory"])
    if loaded.partition.strategy != payload["split_by"]:
        raise SystemExit(
            f"config asks for split_by={payload['split_by']!r} but the build "
            f"in {payload['dataset_directory']} partitioned by "
            f"{loaded.partition.strategy!r}. The build owns the split; change "
            f"the config or rebuild the corpus.")
    return loaded


def command_subsample(arguments):
    summary = subsample.subsample_by_learner(
        arguments.source, arguments.out,
        learners=arguments.learners, seed=arguments.seed,
        learner_column=arguments.learner_column)
    print(json.dumps(summary, indent=2))
    beside = pathlib.Path(arguments.out).with_suffix(".subset.json")
    beside.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nsummary written to {beside}")
    return 0


def command_train(arguments):
    payload = load_config(arguments.config)
    loaded = load_corpus(payload)
    description = loaded.describe()

    print(f"corpus     {description['dataset']} {description['version']}")
    print(f"  rows     {description['interactions']:,}")
    print(f"  learners {description['learners']:,}")
    print(f"  split    {description['split']}")
    print(f"  hashes   raw={description['raw_dataset_hash'][:16]} "
          f"processed={description['processed_hash'][:16]} "
          f"split={description['split_hash'][:16]}")

    # Verified once, up front, against the partition the build committed to.
    splits.assert_no_leakage(loaded.partition)
    print("  leakage  PASS (per-learner ordering re-verified independently)\n")

    records = []
    for config in configs_for(payload):
        if arguments.model and config.model != arguments.model:
            continue
        print(f"training {config.model} ...", flush=True)
        record = experiment.run(config, loaded.rows,
                                partition=loaded.partition,
                                dataset_description=description)
        metrics = record["metrics"]
        print(f"  auc {metrics['auc']:.4f}  acc {metrics['accuracy']:.4f}  "
              f"log loss {metrics['log_loss']:.4f}  "
              f"scored {metrics['test_interactions']:,}")
        print(f"  checkpoint {record['checkpoint']}", flush=True)
        print(f"  epochs     {len(record['training_history']) or 'n/a'}\n",
              flush=True)
        records.append(record)

    return _report(records, payload)


def command_evaluate(arguments):
    """Re-score from checkpoints. Trains nothing, fits nothing."""
    payload = load_config(arguments.config)
    loaded = load_corpus(payload)
    splits.assert_no_leakage(loaded.partition)

    validation_rows = list(loaded.partition.validation)
    tasks = experiment.build_tasks(
        list(loaded.partition.train) + validation_rows
        + list(loaded.partition.test),
        loaded.partition.test)

    records = []
    for config in configs_for(payload):
        if arguments.model and config.model != arguments.model:
            continue
        model = models.build(config.model, **config.parameters)
        suffix = getattr(model, "checkpoint_suffix", ".pt")
        path = experiment.CHECKPOINTS / f"{config.model}_{config.dataset}{suffix}"
        if not path.is_file():
            print(f"{config.model}: no checkpoint at {path} — skipped")
            continue

        model.load(path)
        metrics = experiment.metrics_for(*experiment.score(model, tasks))
        print(f"{config.model:12} auc {metrics['auc']:.4f}  "
              f"acc {metrics['accuracy']:.4f}  "
              f"log loss {metrics['log_loss']:.4f}")
        records.append({"config": {"model": config.model,
                                   "dataset": config.dataset,
                                   "split_by": config.split_by,
                                   "seed": config.seed},
                        "preprocessing_version": config.preprocessing_version,
                        "dataset_fingerprint":
                            experiment.dataset_fingerprint(loaded.rows),
                        "metrics": metrics,
                        "checkpoint": experiment.portable(path)})

    return _report(records, payload, prefix="evaluate_")


def command_compare(arguments):
    """Render the comparison table from records already on disk."""
    payload = load_config(arguments.config)
    records = []
    for name in payload["models"]:
        path = (experiment.RESULTS
                / f"{name}_{payload['dataset']}_{payload['split_by']}.json")
        if path.is_file():
            records.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            print(f"no result for {name} — it has not been trained",
                  file=sys.stderr)
    if not records:
        raise SystemExit("no results to compare")
    print(experiment.comparison_table(records))
    return 0


def _report(records, payload, prefix=""):
    if not records:
        print("nothing ran")
        return 1
    print(experiment.comparison_table(records))
    csv_path = experiment.write_metrics_csv(
        experiment.RESULTS / f"{prefix}{payload['dataset']}_metrics.csv",
        records)
    print(f"\nmetrics written to {csv_path}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kt_research.run_experiment",
        description="Reproducible knowledge-tracing baselines.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sub = subparsers.add_parser("subsample",
                                help="Deterministic learner-level subset.")
    sub.add_argument("--source", required=True)
    sub.add_argument("--out", required=True)
    sub.add_argument("--learners", type=int, required=True)
    sub.add_argument("--seed", type=int, required=True)
    sub.add_argument("--learner-column", default="user_id",
                     dest="learner_column")
    sub.set_defaults(handler=command_subsample)

    for name, handler, help_text in (
            ("train", command_train, "Fit every model in the config."),
            ("evaluate", command_evaluate, "Re-score from checkpoints."),
            ("compare", command_compare, "Render the comparison table.")):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--config", required=True)
        sub.add_argument("--model", default=None,
                         help="Restrict to one model by name.")
        sub.set_defaults(handler=handler)

    arguments = parser.parse_args(argv)
    return arguments.handler(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
