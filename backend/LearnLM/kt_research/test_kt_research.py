"""
The KT research pipeline (M2 P2.11b).

No database, no production import, no network. These tests hold the two
things that make a KT number mean anything: the split does not leak, and a
model is scored only on knowledge derived from earlier interactions.
"""

import random

import pytest

from kt_research import experiment, models, splits


def synthetic(learners=12, per_learner=25, concepts=4, seed=7):
    """
    A generated corpus with a real learning curve, so a model that works
    scores above chance and one that is broken does not.

    Synthetic ON PURPOSE: it is checked into the tests, so the pipeline can
    be verified without downloading a corpus, and no result from it is ever
    reported as a finding.
    """
    rng = random.Random(seed)
    rows, stamp = [], 0
    for learner in range(learners):
        skill = {c: rng.uniform(0.1, 0.4) for c in range(concepts)}
        for _ in range(per_learner):
            concept = rng.randrange(concepts)
            correct = rng.random() < skill[concept]
            skill[concept] = min(0.95, skill[concept] + 0.03)
            stamp += 1
            rows.append({"learner_id": f"L{learner}", "concept": concept,
                         "correct": correct, "timestamp": stamp,
                         "difficulty": 0.5, "response_time": 30.0,
                         "delta_time": 60.0, "attempt_count": 1})
    return rows


# ═════════════════════════════════════════════════════════════
# Leakage — the load-bearing property
# ═════════════════════════════════════════════════════════════

def test_split_by_has_no_default():
    """
    "Next response for a known learner" and "a learner never seen before"
    are different claims. A default would let an experiment answer one while
    reporting the other.
    """
    with pytest.raises(TypeError):
        splits.split(synthetic(), fraction=0.8)


def test_an_unknown_strategy_is_refused():
    with pytest.raises(ValueError, match="no default"):
        splits.split(synthetic(), split_by="random")


def test_a_temporal_split_puts_no_future_in_train():
    result = splits.split(synthetic(), split_by=splits.BY_TIME)

    splits.assert_no_leakage(result)
    assert max(r["timestamp"] for r in result.train) < \
        min(r["timestamp"] for r in result.test)


def test_a_learner_split_shares_no_learner():
    result = splits.split(synthetic(), split_by=splits.BY_LEARNER)

    splits.assert_no_leakage(result)
    assert not ({r["learner_id"] for r in result.train}
                & {r["learner_id"] for r in result.test})


def test_leakage_is_detected_rather_than_assumed():
    """The guard must actually fire — a check that cannot fail is decoration."""
    rows = synthetic()
    bad = splits.Split(train=rows, test=rows, strategy=splits.BY_TIME,
                       boundary=None)

    with pytest.raises(splits.LeakageError, match="overlaps"):
        splits.assert_no_leakage(bad)


def test_learner_leakage_is_detected():
    rows = synthetic()
    bad = splits.Split(train=rows, test=rows, strategy=splits.BY_LEARNER,
                       boundary=None)

    with pytest.raises(splits.LeakageError, match="both splits"):
        splits.assert_no_leakage(bad)


def test_out_of_order_sequences_are_refused():
    scrambled = {"L0": [{"timestamp": 5, "concept": 1, "correct": True},
                        {"timestamp": 2, "concept": 1, "correct": False}]}

    with pytest.raises(splits.LeakageError, match="not in time order"):
        splits.assert_within_sequence_causality(scrambled)


def test_the_split_is_deterministic():
    rows = synthetic()
    first = splits.split(rows, split_by=splits.BY_TIME)
    second = splits.split(list(reversed(rows)), split_by=splits.BY_TIME)

    assert [r["timestamp"] for r in first.train] == \
        [r["timestamp"] for r in second.train]


# ═════════════════════════════════════════════════════════════
# The model ladder
# ═════════════════════════════════════════════════════════════

def test_the_seven_rungs_are_declared():
    assert set(models.SPECS) == {
        "BKT", "DKT", "SAKT", "AKT", "Transformer", "TA-GTKT", "TA-GTKT-P"}


def test_unimplemented_models_refuse_rather_than_returning_a_number():
    """
    A stub returning 0.5 would let a broken pipeline report a plausible AUC.
    """
    for name, spec in models.SPECS.items():
        if spec.implemented:
            continue
        with pytest.raises(NotImplementedError, match="not implemented"):
            models.build(name)


def test_every_unimplemented_model_says_what_it_needs():
    for spec in models.SPECS.values():
        if not spec.implemented:
            assert spec.needs, spec.name


def test_ta_gtkt_consumes_every_input_the_design_names():
    consumed = set(models.SPECS["TA-GTKT"].consumes)
    assert {"concept", "correctness", "timestamp", "response_time",
            "delta_time", "attempt_count", "difficulty"} <= consumed


def test_the_prerequisite_variant_adds_exactly_prerequisites():
    base = set(models.SPECS["TA-GTKT"].consumes)
    extended = set(models.SPECS["TA-GTKT-P"].consumes)
    assert extended - base == {"prerequisites"}


# ═════════════════════════════════════════════════════════════
# BKT
# ═════════════════════════════════════════════════════════════

def test_bkt_refuses_to_score_before_fitting():
    with pytest.raises(models.NotTrainedError):
        models.BKT().predict_sequence([{"concept": 1, "correct": True}])


def test_bkt_predicts_only_from_earlier_interactions():
    """
    The causal contract. Position 0 is predicted with no evidence at all, so
    it must equal the prior-derived value and cannot depend on its own label.
    """
    model = models.BKT().fit({"L0": [{"concept": 1, "correct": True}] * 5})

    right = model.predict_sequence([{"concept": 1, "correct": True}])
    wrong = model.predict_sequence([{"concept": 1, "correct": False}])

    assert right[0] == wrong[0], "the first prediction saw its own label"


def test_bkt_learns_within_a_sequence():
    model = models.BKT().fit({"L0": [{"concept": 1, "correct": True}] * 10})
    predictions = model.predict_sequence(
        [{"concept": 1, "correct": True}] * 6)

    assert predictions[-1] > predictions[0]


def test_slip_and_guess_above_a_half_are_refused():
    with pytest.raises(ValueError, match="below 0.5"):
        models.BKTParameters(slip=0.7).validated()


# ═════════════════════════════════════════════════════════════
# Metrics
# ═════════════════════════════════════════════════════════════

def test_auc_of_a_perfect_ranking_is_one():
    assert models.auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0


def test_auc_of_a_reversed_ranking_is_zero():
    assert models.auc([1, 1, 0, 0], [0.1, 0.2, 0.8, 0.9]) == 0.0


def test_auc_of_a_constant_score_is_a_half():
    assert models.auc([0, 1, 0, 1], [0.5] * 4) == 0.5


def test_auc_is_undefined_with_one_class():
    import math
    assert math.isnan(models.auc([1, 1, 1], [0.1, 0.5, 0.9]))


# ═════════════════════════════════════════════════════════════
# Reproducibility
# ═════════════════════════════════════════════════════════════

def test_a_run_records_everything_needed_to_reproduce_it():
    config = experiment.ExperimentConfig(
        model="BKT", dataset="synthetic", split_by=splits.BY_TIME)

    record = experiment.run(config, synthetic(), save=False)

    assert record["dataset_fingerprint"]
    assert record["split"]["strategy"] == "time"
    assert record["config"]["seed"]
    assert record["metrics"]["test_interactions"] > 0
    assert record["environment"]["python"]


def test_the_same_inputs_give_the_same_numbers():
    config = experiment.ExperimentConfig(
        model="BKT", dataset="synthetic", split_by=splits.BY_TIME)
    rows = synthetic()

    first = experiment.run(config, rows, save=False)
    second = experiment.run(config, rows, save=False)

    assert first["metrics"] == second["metrics"]
    assert first["dataset_fingerprint"] == second["dataset_fingerprint"]


def test_a_different_filter_changes_the_fingerprint():
    """The commonest irreproducibility: same 'dataset', different rows."""
    rows = synthetic()
    assert (experiment.dataset_fingerprint(rows)
            != experiment.dataset_fingerprint(rows[:-1]))


def test_bkt_beats_chance_on_a_corpus_with_a_learning_curve():
    """
    An end-to-end sanity check, not a finding. The corpus is synthetic and
    no number from it is reportable.
    """
    config = experiment.ExperimentConfig(
        model="BKT", dataset="synthetic", split_by=splits.BY_TIME)

    record = experiment.run(config, synthetic(learners=30, per_learner=40),
                            save=False)

    assert record["metrics"]["rmse"] < 0.6


def test_the_pipeline_refuses_an_unsound_split_before_training(monkeypatch):
    """
    The leakage check runs BEFORE fitting, so a bad split yields no number
    at all rather than a number nobody should trust.
    """
    monkeypatch.setattr(
        splits, "split",
        lambda rows, **kw: splits.Split(train=list(rows), test=list(rows),
                                        strategy=splits.BY_TIME,
                                        boundary=None))
    config = experiment.ExperimentConfig(
        model="BKT", dataset="synthetic", split_by=splits.BY_TIME)

    with pytest.raises(splits.LeakageError):
        experiment.run(config, synthetic(), save=False)


def test_the_research_package_never_imports_production_models():
    """
    Separation, asserted. This package must not read learner data: 44
    submissions is not a training set, and a research pipeline that can
    reach production is one that will eventually train on it.
    """
    import pathlib

    root = pathlib.Path(splits.__file__).parent
    for path in root.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        source = path.read_text(encoding="utf-8")
        for forbidden in ("from groups", "import groups", "django"):
            assert forbidden not in source, f"{path.name}: {forbidden}"


def test_no_module_imports_the_application_by_any_route():
    """
    The same separation, checked on the parsed imports rather than on the
    text (M2 P2.12 §22H).

    The substring check above cannot see `importlib.import_module("groups")`
    and trips over the word appearing in prose. This walks the actual import
    statements, so it catches the first and forgives the second — and it
    covers the whole transitive surface a module declares, including the
    corpus loader that exists precisely to sit on this boundary.
    """
    import ast
    import pathlib

    banned = {"groups", "django", "kt_dataset", "LearnLM"}
    offences = []

    for path in sorted(pathlib.Path(splits.__file__).parent.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in banned:
                    offences.append(f"{path.name}:{node.lineno} imports {name}")

    assert not offences, (
        "the research package reached into the application:\n  "
        + "\n  ".join(offences))


# ═════════════════════════════════════════════════════════════
# Three-way and per-learner-history splits (M2 P2.12 §22C)
# ═════════════════════════════════════════════════════════════

def test_a_learner_history_split_produces_three_buckets():
    result = splits.split(synthetic(), split_by=splits.BY_LEARNER_HISTORY,
                          fraction=0.7, validation_fraction=0.15)

    splits.assert_no_leakage(result)
    assert result.train and result.validation and result.test


def test_every_learner_appears_in_all_three_buckets_in_order():
    result = splits.split(synthetic(learners=6, per_learner=20),
                          split_by=splits.BY_LEARNER_HISTORY,
                          fraction=0.7, validation_fraction=0.15)

    for learner in {r["learner_id"] for r in result.train}:
        latest_train = max(r["timestamp"] for r in result.train
                           if r["learner_id"] == learner)
        earliest_test = min(r["timestamp"] for r in result.test
                            if r["learner_id"] == learner)
        assert latest_train < earliest_test


def test_a_learner_too_short_to_cut_goes_entirely_to_train():
    """
    Scoring a learner with no history measures the cold-start prior, which
    is a different experiment with a different claim.
    """
    rows = [{"learner_id": "L0", "concept": 1, "correct": True,
             "timestamp": 1},
            {"learner_id": "L0", "concept": 1, "correct": False,
             "timestamp": 2}]

    result = splits.split(rows, split_by=splits.BY_LEARNER_HISTORY,
                          fraction=0.7, validation_fraction=0.15)

    assert len(result.train) == 2
    assert not result.validation and not result.test


def test_validation_fraction_is_refused_by_strategies_that_cannot_honour_it():
    """
    An experiment that believes it tuned on a validation set and did not is
    worse off than one that never asked for it.
    """
    for strategy in (splits.BY_TIME, splits.BY_LEARNER):
        with pytest.raises(ValueError, match="only defined for"):
            splits.split(synthetic(), split_by=strategy,
                         validation_fraction=0.15)


def test_learner_history_leakage_is_detected_per_learner():
    rows = synthetic(learners=4, per_learner=10)
    bad = splits.Split(train=rows, test=rows,
                       strategy=splits.BY_LEARNER_HISTORY, boundary=None)

    with pytest.raises(splits.LeakageError, match="cannot be both"):
        splits.assert_no_leakage(bad)


def test_an_out_of_order_learner_history_split_is_detected():
    early = [{"learner_id": "L0", "concept": 1, "correct": True,
              "timestamp": t} for t in (1, 2, 3)]
    late = [{"learner_id": "L0", "concept": 1, "correct": True,
             "timestamp": t} for t in (4, 5)]
    # Train holds the LATER half — the model would be told the future.
    bad = splits.Split(train=late, test=early,
                       strategy=splits.BY_LEARNER_HISTORY, boundary=None)

    with pytest.raises(splits.LeakageError, match="must be strictly later"):
        splits.assert_no_leakage(bad)


def test_one_learners_late_training_row_is_not_leakage_for_another_learner():
    """
    A guard that cries wolf gets disabled. With no shared clock, two
    learners' positions are not comparable and a global check would fail on
    a split that is perfectly sound.
    """
    train = [{"learner_id": "A", "concept": 1, "correct": True,
              "timestamp": 900}]
    test = [{"learner_id": "B", "concept": 1, "correct": True,
             "timestamp": 5}]
    sound = splits.Split(train=train, test=test,
                         strategy=splits.BY_LEARNER_HISTORY, boundary=None)

    splits.assert_no_leakage(sound)


def test_the_split_summary_reports_the_validation_bucket():
    result = splits.split(synthetic(), split_by=splits.BY_LEARNER_HISTORY,
                          fraction=0.7, validation_fraction=0.15)

    assert result.summary()["validation"] == len(result.validation)


# ═════════════════════════════════════════════════════════════
# The corpus boundary (M2 P2.12 §22B, §22H)
# ═════════════════════════════════════════════════════════════

def build_corpus(directory):
    """
    A real corpus build, written to disk the way the command writes it.

    Deliberately driven through `kt_dataset` rather than hand-written: the
    thing under test is that the two packages interoperate across a
    directory, and a fixture I wrote myself would agree with whatever I
    assumed the format to be.
    """
    import csv

    from kt_dataset import pipeline, sources
    from kt_dataset.schema import CANONICAL_COLUMNS

    rows = sources.synthetic_rows(learners=12, questions=15, max_length=20)
    config = pipeline.BuildConfig(dataset_name="synthetic",
                                 dataset_version="v1",
                                 source_file="synthetic")
    result = pipeline.build(rows, config, sources.SYNTHETIC, "test-hash")

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "interactions.csv").open(
            "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CANONICAL_COLUMNS))
        writer.writeheader()
        for interaction in result.interactions:
            writer.writerow(interaction.as_row())

    with (directory / "split_assignment.csv").open(
            "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle,
                                fieldnames=list(pipeline.SPLIT_COLUMNS))
        writer.writeheader()
        for row in pipeline.split_assignment(result):
            writer.writerow(row)

    pipeline.write_manifest(
        directory / "manifest.json",
        {"dataset_name": "synthetic", "dataset_version": "v1",
         "raw_dataset_hash": "test-hash",
         "processed_hash": result.processed_hash,
         "split_hash": result.split_hash})
    return result


def test_a_built_corpus_loads_with_its_partition(tmp_path):
    from kt_research import datasets

    built = build_corpus(tmp_path / "corpus")
    loaded = datasets.load_build(tmp_path / "corpus")

    assert len(loaded.rows) == len(built.interactions)
    assert len(loaded.partition.train) == len(built.train)
    assert len(loaded.partition.validation) == len(built.validation)
    assert len(loaded.partition.test) == len(built.test)


def test_the_loaded_partition_is_read_and_not_recomputed(tmp_path):
    """
    A second implementation of the split rule agrees with itself, which is
    not the same as being right. The build owns the partition; this package
    verifies it.
    """
    from kt_research import datasets

    built = build_corpus(tmp_path / "corpus")
    assignment = tmp_path / "corpus" / "split_assignment.csv"

    # Move one row from train to test. A recomputation would put it back.
    text = assignment.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(text[1:], start=1):
        if line.endswith(",train"):
            text[index] = f"{line.rsplit(',', 1)[0]},test"
            break
    else:
        pytest.fail("the fixture produced no training rows")
    assignment.write_text("\n".join(text) + "\n", encoding="utf-8")

    loaded = datasets.load_build(tmp_path / "corpus")

    assert len(loaded.partition.train) == len(built.train) - 1
    assert len(loaded.partition.test) == len(built.test) + 1


def test_a_corpus_loaded_from_a_build_passes_the_leakage_guard(tmp_path):
    from kt_research import datasets

    build_corpus(tmp_path / "corpus")
    loaded = datasets.load_build(tmp_path / "corpus")

    splits.assert_no_leakage(loaded.partition)


def test_an_incomplete_build_directory_is_refused(tmp_path):
    from kt_research import datasets

    build_corpus(tmp_path / "corpus")
    (tmp_path / "corpus" / "split_assignment.csv").unlink()

    with pytest.raises(datasets.BuildNotUsable, match="split_assignment"):
        datasets.load_build(tmp_path / "corpus")


def test_a_split_from_a_different_build_is_refused(tmp_path):
    """
    A partition from one corpus applied to another is not a split.
    """
    from kt_research import datasets

    build_corpus(tmp_path / "corpus")
    assignment = tmp_path / "corpus" / "split_assignment.csv"
    lines = assignment.read_text(encoding="utf-8").splitlines()
    lines.append("synthetic:99999,L99,1,test")
    assignment.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(datasets.BuildNotUsable, match="different builds"):
        datasets.load_build(tmp_path / "corpus")


def test_an_unassigned_interaction_is_refused(tmp_path):
    from kt_research import datasets

    build_corpus(tmp_path / "corpus")
    assignment = tmp_path / "corpus" / "split_assignment.csv"
    lines = assignment.read_text(encoding="utf-8").splitlines()
    assignment.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    with pytest.raises(datasets.BuildNotUsable, match="split assignments"):
        datasets.load_build(tmp_path / "corpus")


def test_every_field_the_brief_names_is_accounted_for():
    """
    P2.12 §22B: document what a corpus cannot supply rather than invent it.
    """
    from kt_research import datasets

    named = {"learner_id", "question_id", "concept_id", "correctness",
             "timestamp", "response_time", "difficulty"}
    assert named == set(datasets.FIELD_AVAILABILITY)

    for field_name, (status, reason) in datasets.FIELD_AVAILABILITY.items():
        assert status in datasets.STATUSES, field_name
        assert reason, field_name
        if status != datasets.AVAILABLE:
            # "not available" on its own is what gets a field quietly
            # fabricated three phases later. The reason has to be a reason.
            assert len(reason) > 60, (
                f"{field_name} is not available and says why not")


def test_response_time_and_difficulty_are_declared_unavailable():
    """
    Both are tempting to fabricate — one exists upstream, the other can be
    estimated from labels. Neither is invented here.
    """
    from kt_research import datasets

    for field_name in ("response_time", "difficulty"):
        status, _reason = datasets.FIELD_AVAILABILITY[field_name]
        assert status == datasets.UNAVAILABLE, field_name


def test_time_is_recorded_as_an_order_and_not_as_a_clock():
    """
    Neither "available" nor "unavailable" is true of `timestamp` here. The
    first would let a later phase reach for elapsed time that does not
    exist; the second would suggest the corpus cannot be sequenced, and
    sequencing it is the entire basis of the split.
    """
    from kt_research import datasets

    status, _reason = datasets.FIELD_AVAILABILITY["timestamp"]
    assert status == datasets.ORDINAL_ONLY


# ═════════════════════════════════════════════════════════════
# The model registry (M2 P2.12 §22D)
# ═════════════════════════════════════════════════════════════

def test_building_a_model_returns_that_model_and_not_a_substitute():
    """
    The registry used to return BKT for anything marked implemented, which
    would have reported BKT's numbers under DKT's name.
    """
    for name, spec in models.SPECS.items():
        if not spec.implemented:
            continue
        assert models.build(name).name == name


def test_every_implemented_model_has_a_constructor():
    for name, spec in models.SPECS.items():
        if spec.implemented:
            assert name in models.CONSTRUCTORS, name


def test_an_implemented_model_without_a_constructor_refuses(monkeypatch):
    monkeypatch.delitem(models.CONSTRUCTORS, "BKT")

    with pytest.raises(NotImplementedError, match="no constructor"):
        models.build("BKT")


def test_the_concept_accessor_takes_either_name():
    assert models.concept_of({"concept_id": "A"}) == "A"
    assert models.concept_of({"concept": 7}) == 7


def test_an_empty_concept_id_is_a_value_not_an_absence():
    """`""` means the source has no concept mapping — different from missing."""
    assert models.concept_of({"concept_id": "", "concept": "fallback"}) == ""


def test_a_row_with_no_concept_at_all_is_refused():
    with pytest.raises(KeyError):
        models.concept_of({"correct": True})


# ═════════════════════════════════════════════════════════════
# Log loss (M2 P2.12 §22D)
# ═════════════════════════════════════════════════════════════

def test_log_loss_punishes_confident_wrongness_that_auc_cannot_see():
    """
    AUC is invariant to any monotone rescaling, so it rates a wildly
    overconfident model identically to a calibrated one with the same
    ranking. For deciding what to show a learner, calibration is the part
    that matters.
    """
    labels = [1, 1, 0, 0]
    calibrated = [0.6, 0.55, 0.45, 0.4]
    overconfident = [0.999, 0.998, 0.002, 0.001]
    reversed_confidence = [0.001, 0.002, 0.998, 0.999]

    assert models.auc(labels, calibrated) == models.auc(labels, overconfident)
    assert models.log_loss(labels, overconfident) < \
        models.log_loss(labels, calibrated)
    assert models.log_loss(labels, reversed_confidence) > 5.0


def test_log_loss_is_finite_even_for_a_certain_wrong_answer():
    """Unclamped, one row would decide the metric for the whole corpus."""
    import math

    value = models.log_loss([1], [0.0])
    assert math.isfinite(value)


def test_a_perfect_prediction_has_almost_no_log_loss():
    assert models.log_loss([1, 0], [1.0, 0.0]) < 1e-5


# ═════════════════════════════════════════════════════════════
# Scoring protocol (M2 P2.12 §22D)
# ═════════════════════════════════════════════════════════════

def test_scored_rows_keep_their_earlier_history_as_context():
    """
    A learner's history is not restarted at the test boundary. Without this,
    the table compares three cold-start priors and calls it knowledge
    tracing.
    """
    rows = synthetic(learners=3, per_learner=12)
    result = splits.split(rows, split_by=splits.BY_LEARNER_HISTORY,
                          fraction=0.7, validation_fraction=0.15)

    tasks = experiment.build_tasks(
        result.train + result.validation + result.test, result.test)

    for sequence, indices in tasks:
        assert min(indices) > 0, "a scored row was given no history"
        assert len(sequence) > len(indices)


def test_a_scored_row_never_appears_before_its_own_context():
    rows = synthetic(learners=3, per_learner=12)
    result = splits.split(rows, split_by=splits.BY_LEARNER_HISTORY,
                          fraction=0.7, validation_fraction=0.15)

    tasks = experiment.build_tasks(
        result.train + result.validation + result.test, result.test)

    for sequence, indices in tasks:
        stamps = [row["timestamp"] for row in sequence]
        assert stamps == sorted(stamps)
        assert indices == sorted(indices)


def test_the_validation_task_never_contains_a_test_row():
    """Early stopping on a set that includes test rows is tuning on test."""
    rows = synthetic(learners=4, per_learner=15)
    result = splits.split(rows, split_by=splits.BY_LEARNER_HISTORY,
                          fraction=0.7, validation_fraction=0.15)

    test_keys = {(r["learner_id"], r["timestamp"]) for r in result.test}
    tasks = experiment.build_tasks(
        result.train + result.validation, result.validation)

    for sequence, _indices in tasks:
        for row in sequence:
            assert (row["learner_id"], row["timestamp"]) not in test_keys


def test_the_comparison_table_holds_only_models_that_ran():
    """
    A blank cell invites the reader to fill it in from expectation, which is
    how a result nobody measured ends up being cited.
    """
    record = experiment.run(
        experiment.ExperimentConfig(model="BKT", dataset="synthetic",
                                    split_by=splits.BY_TIME),
        synthetic(), save=False, checkpoint=False)

    table = experiment.comparison_table([record])

    assert "BKT" in table
    assert "DKT" not in table
    assert "Transformer" not in table


# ═════════════════════════════════════════════════════════════
# BKT checkpointing (M2 P2.12 §22G)
# ═════════════════════════════════════════════════════════════

def test_a_run_that_is_not_recorded_leaves_no_checkpoint_behind(tmp_path,
                                                                monkeypatch):
    """
    `save=False` marks a run as not an artifact. Until this default was tied
    to `save`, the test suite itself wrote model weights into the working
    tree every time it ran.
    """
    monkeypatch.setattr(experiment, "CHECKPOINTS", tmp_path / "checkpoints")

    experiment.run(
        experiment.ExperimentConfig(model="BKT", dataset="synthetic",
                                    split_by=splits.BY_TIME),
        synthetic(), save=False)

    assert not (tmp_path / "checkpoints").exists()


def test_a_recorded_run_does_write_a_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "CHECKPOINTS", tmp_path / "checkpoints")
    monkeypatch.setattr(experiment, "RESULTS", tmp_path / "results")

    record = experiment.run(
        experiment.ExperimentConfig(model="BKT", dataset="synthetic",
                                    split_by=splits.BY_TIME),
        synthetic(), save=True)

    assert record["checkpoint"]


def test_a_recorded_path_is_portable(tmp_path, monkeypatch):
    """
    A run record is a committed artifact in a public repository. An absolute
    path pins it to one machine, publishes that machine's layout, and is
    useless to anyone re-running the experiment elsewhere.
    """
    import pathlib

    monkeypatch.setattr(experiment, "CHECKPOINTS",
                        experiment.PACKAGE_ROOT / "kt_research" / "tmpckpt")
    monkeypatch.setattr(experiment, "RESULTS", tmp_path / "results")

    record = experiment.run(
        experiment.ExperimentConfig(model="BKT", dataset="portability-probe",
                                    split_by=splits.BY_TIME),
        synthetic(), save=True)

    written = pathlib.Path(record["checkpoint"])
    assert not written.is_absolute(), record["checkpoint"]
    assert "\\" not in record["checkpoint"]
    assert (experiment.PACKAGE_ROOT / written).is_file()

    written.unlink(missing_ok=True) if written.is_absolute() else \
        (experiment.PACKAGE_ROOT / written).unlink(missing_ok=True)
    (experiment.PACKAGE_ROOT / "kt_research" / "tmpckpt").rmdir()


def test_a_path_outside_the_project_is_left_alone():
    """Better an absolute path than a wrong relative one."""
    assert experiment.portable("/somewhere/else/model.pt").endswith("model.pt")


def test_a_bkt_checkpoint_round_trips(tmp_path):
    rows = synthetic()
    sequences = experiment.sequences_by_learner(rows)
    original = models.BKT().fit(sequences)
    original.save(tmp_path / "bkt.json")

    restored = models.BKT().load(tmp_path / "bkt.json")

    assert original.predict_sequence(rows[:20]) == \
        restored.predict_sequence(rows[:20])


def test_an_integer_concept_survives_a_checkpoint(tmp_path):
    """
    JSON object keys are always strings. Keyed by concept, an integer id
    would come back as a string and every interaction would silently score
    against the default prior.
    """
    sequences = experiment.sequences_by_learner(synthetic())
    models.BKT().fit(sequences).save(tmp_path / "bkt.json")

    restored = models.BKT().load(tmp_path / "bkt.json")

    assert all(isinstance(concept, int) for concept in restored.per_concept)


def test_a_checkpoint_from_another_model_is_refused(tmp_path):
    import json

    (tmp_path / "wrong.json").write_text(
        json.dumps({"model": "DKT", "default": {}, "per_concept": []}),
        encoding="utf-8")

    with pytest.raises(ValueError, match="checkpoint holds"):
        models.BKT().load(tmp_path / "wrong.json")


# ═════════════════════════════════════════════════════════════
# Subsampling (M2 P2.12 §22I)
# ═════════════════════════════════════════════════════════════

def raw_csv(path, learners=20, rows_each=6):
    import csv

    with path.open("w", encoding="latin-1", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["order_id", "user_id", "problem_id",
                                "skill_id", "correct"])
        writer.writeheader()
        order = 0
        for learner in range(learners):
            for _ in range(rows_each):
                order += 1
                writer.writerow({"order_id": order, "user_id": f"U{learner}",
                                 "problem_id": order % 7, "skill_id": 3,
                                 "correct": order % 2})
    return path


def test_a_subsample_keeps_whole_learners(tmp_path):
    """
    Sampling rows would cut sequences in half, which is not a smaller sample
    of the same problem — it is a different and easier one.
    """
    from kt_research import subsample

    source = raw_csv(tmp_path / "raw.csv", learners=20, rows_each=6)
    summary = subsample.subsample_by_learner(
        source, tmp_path / "subset.csv", learners=5, seed=1,
        learner_column="user_id")

    assert summary["learners_kept"] == 5
    assert summary["rows_kept"] == 30, "a learner lost part of their history"


def test_the_same_seed_selects_the_same_learners(tmp_path):
    from kt_research import subsample

    source = raw_csv(tmp_path / "raw.csv")
    first = subsample.subsample_by_learner(
        source, tmp_path / "a.csv", learners=5, seed=7,
        learner_column="user_id")
    second = subsample.subsample_by_learner(
        source, tmp_path / "b.csv", learners=5, seed=7,
        learner_column="user_id")

    assert first["subset_sha256"] == second["subset_sha256"]


def test_a_different_seed_selects_different_learners(tmp_path):
    from kt_research import subsample

    source = raw_csv(tmp_path / "raw.csv")
    first = subsample.subsample_by_learner(
        source, tmp_path / "a.csv", learners=5, seed=1,
        learner_column="user_id")
    second = subsample.subsample_by_learner(
        source, tmp_path / "b.csv", learners=5, seed=2,
        learner_column="user_id")

    assert first["subset_sha256"] != second["subset_sha256"]


def test_the_subset_records_its_own_hash_and_seed(tmp_path):
    """A subset that existed only as a flag is invisible in the corpus hash."""
    from kt_research import subsample

    source = raw_csv(tmp_path / "raw.csv")
    summary = subsample.subsample_by_learner(
        source, tmp_path / "subset.csv", learners=5, seed=3,
        learner_column="user_id")

    assert summary["seed"] == 3
    assert len(summary["subset_sha256"]) == 64
    assert summary["source_sha256"] != summary["subset_sha256"]


def test_asking_for_more_learners_than_exist_keeps_them_all(tmp_path):
    from kt_research import subsample

    source = raw_csv(tmp_path / "raw.csv", learners=8)
    summary = subsample.subsample_by_learner(
        source, tmp_path / "subset.csv", learners=500, seed=1,
        learner_column="user_id")

    assert summary["learners_kept"] == 8
    assert summary["is_whole_corpus"] is True


# ═════════════════════════════════════════════════════════════
# Neural baselines (M2 P2.12 §22D, §22E)
# ═════════════════════════════════════════════════════════════

def small_neural(kind, **overrides):
    parameters = {"hidden": 16, "embedding": 16, "max_length": 20,
                  "epochs": 2, "batch_size": 8, "seed": 5}
    parameters.update(overrides)
    return models.build(kind, **parameters)


def fitted(kind, rows=None, **overrides):
    rows = rows if rows is not None else synthetic(learners=8, per_learner=15)
    model = small_neural(kind, **overrides)
    return model.fit(experiment.sequences_by_learner(rows)), rows


@pytest.mark.parametrize("kind", ["DKT", "Transformer"])
def test_a_neural_model_refuses_to_score_before_fitting(kind):
    pytest.importorskip("torch")

    with pytest.raises(models.NotTrainedError):
        small_neural(kind).predict_sequence(
            [{"concept": 1, "correct": True}])


@pytest.mark.parametrize("kind", ["DKT", "Transformer"])
def test_a_prediction_cannot_see_its_own_label(kind):
    """
    The load-bearing property. Flipping the answer at position k must leave
    every prediction up to and including k untouched — the label is fed at
    k+1 and nowhere earlier, so the information is not in the tensor at all.
    """
    pytest.importorskip("torch")

    model, rows = fitted(kind)
    sequence = rows[:10]
    flipped = [dict(row) for row in sequence]
    flipped[3]["correct"] = not flipped[3]["correct"]

    before = model.predict_sequence(sequence)
    after = model.predict_sequence(flipped)

    assert before[:4] == pytest.approx(after[:4], abs=1e-9)


@pytest.mark.parametrize("kind", ["DKT", "Transformer"])
def test_the_first_position_is_scored_without_any_history(kind):
    """
    All three models must score the same rows. If the sequence models
    skipped position 0, the comparison against BKT would be between
    different test sets.
    """
    pytest.importorskip("torch")

    model, rows = fitted(kind)
    predictions = model.predict_sequence(rows[:6])

    assert len(predictions) == 6
    assert all(0.0 <= p <= 1.0 for p in predictions)


@pytest.mark.parametrize("kind", ["DKT", "Transformer"])
def test_a_sequence_longer_than_the_window_is_fully_scored(kind):
    pytest.importorskip("torch")

    model, rows = fitted(kind, max_length=8)
    long_sequence = rows[:40]

    predictions = model.predict_sequence(long_sequence)

    assert len(predictions) == 40
    assert all(p is not None for p in predictions)


@pytest.mark.parametrize("kind", ["DKT", "Transformer"])
def test_a_checkpoint_reproduces_the_same_scores(kind, tmp_path):
    """
    §22G: `evaluate` re-scores from disk. If it disagreed with `train`, every
    number in the phase would be provisional.
    """
    pytest.importorskip("torch")

    model, rows = fitted(kind)
    path = model.save(tmp_path / f"{kind}.pt")

    restored = small_neural(kind).load(path)

    assert restored.predict_sequence(rows[:15]) == pytest.approx(
        model.predict_sequence(rows[:15]), abs=1e-9)


def test_a_checkpoint_is_not_loaded_into_the_wrong_architecture(tmp_path):
    pytest.importorskip("torch")

    model, _rows = fitted("DKT")
    path = model.save(tmp_path / "dkt.pt")

    with pytest.raises(ValueError, match="checkpoint holds"):
        small_neural("Transformer").load(path)


def test_the_vocabulary_is_built_from_training_rows_only():
    """
    Fitting the vocabulary on the whole corpus gives the model an embedding
    slot for an item it is about to be tested on and was never taught.
    """
    neural = pytest.importorskip("kt_research.neural")

    train = {"L0": [{"concept": "a", "correct": 1},
                    {"concept": "b", "correct": 0}]}
    vocabulary = neural.Vocabulary.from_sequences(train)

    assert vocabulary.encode({"concept": "a"}) != vocabulary.unknown
    assert vocabulary.encode({"concept": "zzz"}) == vocabulary.unknown


def test_scoring_windows_cover_every_position_with_context():
    neural = pytest.importorskip("kt_research.neural")

    for length in (1, 5, 20, 21, 57, 200, 201, 640):
        windows = neural.scoring_windows(length, 20)
        covered = []
        for start, end, score_from in windows:
            assert start <= score_from < end or length == 0
            covered.extend(range(score_from, end))
            if score_from > 0:
                assert score_from - start >= 10, "too little context carried"
        assert covered == list(range(length)), length


def test_training_chunks_cover_every_position_exactly_once():
    neural = pytest.importorskip("kt_research.neural")

    for length in (1, 19, 20, 21, 100):
        seen = []
        for start, end in neural.training_chunks(length, 20):
            seen.extend(range(start, end))
        assert seen == list(range(length)), length


def test_the_transformer_baseline_carries_no_temporal_or_prerequisite_input():
    """
    §22E stops at the plain encoder. A baseline that quietly included half
    of its own successor's additions would make them look free.

    Docstrings are stripped before the check: this module has to be able to
    SAY that it excludes elapsed time without that sentence being read as
    evidence it uses it.
    """
    import ast
    import inspect

    neural = pytest.importorskip("kt_research.neural")
    tree = ast.parse(inspect.getsource(neural))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            body.pop(0)
            if not body:
                body.append(ast.Pass())

    code = ast.unparse(tree)
    for forbidden in ("response_time", "delta_time", "lag_seconds",
                      "prerequisite", "elapsed", "occurred_at"):
        assert forbidden not in code, forbidden


def test_the_declared_inputs_of_the_baselines_are_only_concept_and_outcome():
    for name in ("DKT", "Transformer"):
        assert set(models.SPECS[name].consumes) == {"concept", "correctness"}
