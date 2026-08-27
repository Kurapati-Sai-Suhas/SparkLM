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
