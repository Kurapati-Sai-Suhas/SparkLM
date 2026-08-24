"""
Build a KT interaction dataset (M2 P2.10b).

Writes ONLY to the output directory given by `--out`: a processed CSV and a
JSON manifest. It performs no database writes of any kind, and a structural
test asserts that.

    # validate the pipeline with no licensed data at all
    python manage.py kt_dataset_build --source synthetic --out build/kt/synth

    # a file the operator obtained themselves, under its own terms
    python manage.py kt_dataset_build --source assistments-2009-2010-skill-builder \\
        --input /path/to/skill_builder_data_corrected.csv --out build/kt/assist09

    # LearnLM's own eligible interactions (currently zero)
    python manage.py kt_dataset_build --source learnlm --out build/kt/learnlm

**No dataset is downloaded.** ASSISTments' terms require a written agreement
and prohibit redistribution, so acquisition is an operator action; this command
consumes a file that already exists and records its hash.
"""

import csv
import json
import pathlib

from django.core.management.base import BaseCommand, CommandError

from kt_dataset import adapters, pipeline, sources, stats
from kt_dataset.schema import CANONICAL_COLUMNS


class Command(BaseCommand):
    help = ("Build a reproducible offline KT interaction dataset from a public "
            "benchmark, synthetic data, or LearnLM's eligible submissions.")

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True,
                            help="synthetic | learnlm | "
                                 "assistments-2009-2010-skill-builder")
        parser.add_argument("--input", metavar="PATH",
                            help="Raw file, for file-backed sources.")
        parser.add_argument("--out", required=True, metavar="DIR",
                            help="Output directory for the CSV and manifest.")
        parser.add_argument("--require-concept", action="store_true",
                            help="Reject rows with no concept id. ASSISTments "
                                 "2009 has ~16%% missing skill_id, so this "
                                 "materially changes the dataset — it is "
                                 "recorded in the manifest either way.")
        parser.add_argument("--train-fraction", type=float, default=0.7)
        parser.add_argument("--validation-fraction", type=float, default=0.15)
        parser.add_argument("--min-learner-length", type=int, default=1)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        source = options["source"]
        rows, capabilities, raw_hash, source_file = self._load(source, options)

        config = pipeline.BuildConfig(
            dataset_name=capabilities.name,
            dataset_version=capabilities.version,
            source_file=source_file,
            require_concept=options["require_concept"],
            train_fraction=options["train_fraction"],
            validation_fraction=options["validation_fraction"],
            min_learner_length=options["min_learner_length"],
        )

        result = pipeline.build(rows, config, capabilities, raw_hash)
        leakage = pipeline.audit_split_ordinally(
            result.train, result.validation, result.test)
        statistics = stats.describe(result)
        payload = pipeline.manifest(result, statistics, leakage)

        out = pathlib.Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)
        self._write_csv(out / "interactions.csv", result.interactions)
        self._write_rejections(out / "rejections.csv", result.rejected)
        pipeline.write_manifest(out / "manifest.json", payload)

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, default=str))
        else:
            self._render(payload, out, leakage, result)

    # ── loading ───────────────────────────────────────────────────────

    def _load(self, source, options):
        if source == "synthetic":
            self.stdout.write(self.style.WARNING(
                "SYNTHETIC source — validates the pipeline only. These rows "
                "are\ngenerated from a seeded RNG and are NOT evidence about "
                "any learner."))
            return (sources.synthetic_rows(), sources.SYNTHETIC,
                    "synthetic-no-raw-file", "synthetic")

        if source == "learnlm":
            capabilities = adapters.learnlm_capabilities()
            return (list(adapters.read_learnlm()), capabilities,
                    "learnlm-live-no-raw-file", "CodeSubmission")

        if source not in sources.REGISTRY:
            raise CommandError(
                f"unknown source {source!r}; known: "
                f"{sorted(sources.REGISTRY) + ['learnlm']}")

        capabilities, reader = sources.REGISTRY[source]
        if reader is None:
            raise CommandError(f"{source} has no file reader")

        path = options.get("input")
        if not path:
            raise CommandError(f"--input is required for {source}")
        location = pathlib.Path(path)
        if not location.is_file():
            raise CommandError(
                f"no such file: {path}\n\n"
                f"This command does NOT download datasets. ASSISTments' terms "
                f"of use require a written agreement, prohibit redistribution, "
                f"and require publishing your source code — acquisition is an "
                f"operator decision, not an automated step. See "
                f"docs/P2_10B_KT_DATASET.md.")

        raw_hash = sources.hash_file(location)
        return (list(reader(location, raw_hash=raw_hash)), capabilities,
                raw_hash, location.name)

    # ── output ────────────────────────────────────────────────────────

    def _write_csv(self, path, interactions):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(CANONICAL_COLUMNS))
            writer.writeheader()
            for interaction in interactions:
                writer.writerow(interaction.as_row())

    def _write_rejections(self, path, rejections):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["source_row_id", "reason", "detail"])
            writer.writeheader()
            for rejection in rejections:
                writer.writerow(rejection.as_dict())

    def _render(self, payload, out, leakage, result):
        write, style = self.stdout.write, self.style
        counts = payload["counts"]

        write(style.MIGRATE_HEADING(
            f"KT DATASET BUILD — {payload['dataset_name']} "
            f"{payload['dataset_version']}"))
        write(f"  raw hash        {payload['raw_dataset_hash']}")
        write(f"  processed hash  {payload['processed_hash']}")
        write(f"  split hash      {payload['split_hash']}")
        write("")

        write(style.MIGRATE_HEADING("Counts"))
        for key in ("interactions", "train", "validation", "test", "rejected"):
            write(f"  {key:14} {counts[key]}")
        if payload["rejection_counts"]:
            write("  rejections by reason:")
            for reason, number in sorted(payload["rejection_counts"].items()):
                write(f"    {reason:26} {number}")
        write("")

        statistics = payload["statistics"]
        if statistics.get("interactions"):
            write(style.MIGRATE_HEADING("Statistics"))
            write(f"  learners        {statistics['learners']}")
            write(f"  questions       {statistics['questions']}")
            write(f"  concepts        {statistics['concepts']}")
            write(f"  correct rate    {statistics['correctness']['rate']}")
            write(f"  seq length      {statistics['sequence_length']['buckets']}")
            write(f"  cold start <5   "
                  f"{statistics['cold_start']['learners_lt_5']} learners "
                  f"({statistics['cold_start']['share_of_interactions_in_lt_5_learners']:.1%} "
                  f"of interactions)")
            write("")

        write(style.MIGRATE_HEADING("Leakage audit (P2.10a)"))
        for check in leakage.checks_run:
            write(f"  checked: {check}")
        if not result.interactions:
            write(style.WARNING(
                "  VACUOUSLY SAFE — the dataset is empty. This is NOT evidence "
                "that\n  the split is leakage-free."))
        elif leakage.is_safe:
            write(style.SUCCESS(
                f"  SAFE — {counts['interactions']} interactions across "
                f"{statistics.get('learners', 0)} learners audited"))
        else:
            for problem in leakage.problems[:20]:
                write(style.ERROR(f"  {problem}"))
        write("")

        capabilities = payload["capabilities"]
        if capabilities.get("unavailable_reasons"):
            write(style.MIGRATE_HEADING("Unavailable features"))
            for name, reason in sorted(
                    capabilities["unavailable_reasons"].items()):
                write(f"  {name}:")
                for line in _wrap(reason, 68):
                    write(f"    {line}")
            write("")

        write(f"Written to {out}/ (interactions.csv, rejections.csv, "
              f"manifest.json)")


def _wrap(text, width):
    import textwrap
    return textwrap.wrap(text, width) or [""]
