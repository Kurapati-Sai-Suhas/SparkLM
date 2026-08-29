"""
The model ladder (M2 P2.11b).

Seven rungs, in the order the ablation needs them. Each is registered with
what it consumes, so the comparison is between models that genuinely differ
rather than between names.

    BKT              per-concept, no sequence model      IMPLEMENTED
    DKT              LSTM over interactions              IMPLEMENTED
    SAKT             self-attention, key/query on skill  NOT IMPLEMENTED
    AKT              monotonic attention + Rasch         NOT IMPLEMENTED
    Transformer      plain encoder baseline              IMPLEMENTED
    Transformer+T    + response time, additive fusion    IMPLEMENTED
    Transformer+TG   + learned gated fusion              IMPLEMENTED
    TA-GTKT          + question embedding                IMPLEMENTED
    TA-GTKT-P        + prerequisite signal               NOT IMPLEMENTED

The last four form the P2.13 ablation ladder, and each rung switches on
exactly ONE thing, so a difference in the table has exactly one candidate
cause. The order is deliberate: response time first, then the gate that
mixes it, then the question embedding — which is the largest block of
parameters in the model and the one most likely to overfit, so it is added
last where its effect can still be seen on its own.

Anything unimplemented raises `NotImplementedError` with what it still
needs. A stub that silently returned 0.5 would let a broken pipeline report a
plausible AUC, which is the failure this file exists to avoid.

── On the name ─────────────────────────────────────────────────────────────

TA-GTKT is a NAME FOR THIS CONFIGURATION, not a claim of novelty. Temporal
encoding, gated fusion and prerequisite priors all appear in the KT
literature; what this project can honestly claim is a controlled comparison
of them on one dataset with one split. Any stronger claim needs a literature
review that has not been done.
"""

import math
from dataclasses import asdict as dataclasses_asdict
from dataclasses import dataclass, field


class NotTrainedError(Exception):
    """Scored before fitting. Always a bug in the runner, never in the data."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    implemented: bool
    consumes: tuple
    description: str
    needs: str = ""


SPECS = {s.name: s for s in (
    ModelSpec("BKT", True, ("concept", "correctness"),
              "Bayesian Knowledge Tracing: four parameters per concept."),
    ModelSpec("DKT", True, ("concept", "correctness"),
              "Deep Knowledge Tracing: an LSTM over the interaction "
              "sequence, one output unit per concept."),
    ModelSpec("SAKT", False, ("concept", "correctness"),
              "Self-Attentive KT: attention from past interactions to the "
              "queried skill.",
              needs="a tensor framework and a public dataset"),
    ModelSpec("AKT", False, ("question", "concept", "correctness"),
              "Attentive KT: monotonic attention with Rasch-style difficulty "
              "embeddings. Difficulty is LEARNED as a scalar per question, "
              "not read from a column — which is why AKT can use it on a "
              "corpus that has no difficulty field.",
              needs="a tensor framework and a public dataset"),
    ModelSpec("Transformer", True, ("concept", "correctness"),
              "Plain causal encoder baseline — the control the additions are "
              "measured against. No temporal gating, no prerequisites."),
    ModelSpec("Transformer+T", True,
              ("concept", "correctness", "response_time", "attempt_number",
               "ordinal_gap"),
              "Baseline + response time, fused by plain addition. The "
              "no-gate control."),
    ModelSpec("Transformer+TG", True,
              ("concept", "correctness", "response_time", "attempt_number",
               "ordinal_gap"),
              "The same temporal features mixed by a LEARNED gate. Differs "
              "from the rung below by the gating mechanism and nothing "
              "else."),
    ModelSpec("TA-GTKT", True,
              ("question", "concept", "correctness", "response_time",
               "attempt_number", "ordinal_gap"),
              "Temporal-Aware Gated Transformer KT: question, concept, "
              "interaction and response-time representations -> learned "
              "gated fusion -> causal encoder -> next-response probability."),
    ModelSpec("TA-GTKT-P", False,
              ("question", "concept", "correctness", "response_time",
               "attempt_number", "ordinal_gap", "prerequisites"),
              "TA-GTKT plus a prerequisite signal derived from the "
              "curriculum DAG.",
              needs="TA-GTKT, plus the research prerequisite representation"),
)}

#: Inputs the M2 P2.11b design named for TA-GTKT that the corpus cannot
#: supply, and why (M2 P2.13 §23B).
#:
#: The original spec was written before any real data was in hand, and it
#: named three inputs ASSISTments 2009 does not have. Leaving them in
#: `consumes` would have been a standing claim that the model reads them.
UNSUPPLIED_INPUTS = {
    "delta_time":
        "Real inter-event time requires a wall clock, and this corpus has "
        "none. `ordinal_gap` counts other logged interactions in between; it "
        "is used instead and is never called elapsed time.",
    "difficulty":
        "Not a source column. Estimating it from corpus-wide success rate "
        "would carry test labels into a training feature.",
    "attempt_count":
        "The source column records how many tries the learner ULTIMATELY "
        "needed, so reading it at its own position leaks the outcome. "
        "`attempt_number` — prior attempts, computed from history — is used "
        "instead.",
}

#: Name -> constructor, for the implemented rungs only.
#:
#: Neural constructors import `kt_research.neural` lazily so that BKT, the
#: splitter and the leakage guard all keep working with no tensor framework
#: installed. A leakage check that cannot run without a GPU-era dependency is
#: a leakage check that stops being run.
CONSTRUCTORS = {}


def concept_of(row):
    """
    The concept key, under either of its two names.

    A built corpus carries `concept_id`, the canonical schema's name; the
    synthetic fixtures carry `concept`. One accessor is cheaper than renaming
    a key across 300,000 rows on load, and cheaper than two code paths.

    `is None` rather than truthiness: `""` is a real value here — it means the
    source has no concept mapping for this row, which is different from the
    key being absent.
    """
    value = row.get("concept_id", None)
    if value is None:
        value = row.get("concept", None)
    if value is None:
        raise KeyError("row has neither 'concept_id' nor 'concept'")
    return value


def build(name, **parameters):
    """
    Construct one model by name.

    The constructor is looked up per model rather than defaulted. An earlier
    version returned `BKT(...)` for anything marked implemented, which would
    have silently reported BKT's numbers under DKT's name the moment a second
    model landed — the exact class of error this package's tests exist for.
    """
    spec = SPECS.get(name)
    if spec is None:
        raise KeyError(f"unknown model {name!r}; known: {sorted(SPECS)}")
    if not spec.implemented:
        raise NotImplementedError(
            f"{name} is declared but not implemented. It needs: "
            f"{spec.needs}. Returning a placeholder score here would let a "
            f"broken pipeline report a plausible number.")

    constructor = CONSTRUCTORS.get(name)
    if constructor is None:
        raise NotImplementedError(
            f"{name} is marked implemented but has no constructor registered "
            f"in CONSTRUCTORS. Refusing rather than substituting another "
            f"model.")
    return constructor(**parameters)


def _build_dkt(**parameters):
    from kt_research import neural
    return neural.DKT(**parameters)


def _build_transformer(**parameters):
    from kt_research import neural
    return neural.TransformerKT(**parameters)


def _build_transformer_temporal(**parameters):
    from kt_research import neural
    return neural.TransformerTemporal(**parameters)


def _build_transformer_gated(**parameters):
    from kt_research import neural
    return neural.TransformerGated(**parameters)


def _build_ta_gtkt(**parameters):
    from kt_research import neural
    return neural.TAGTKT(**parameters)


# ═════════════════════════════════════════════════════════════
# BKT — the one that is real
# ═════════════════════════════════════════════════════════════

@dataclass
class BKTParameters:
    """
    The four classical parameters.

    `slip` and `guess` are capped below 0.5 because above it the model is
    better described as inverted — a "known" state that more often produces
    a wrong answer is a labelling error, not a fit.
    """

    prior: float = 0.25        # P(knew it before any evidence)
    learn: float = 0.15        # P(not-known -> known) per opportunity
    slip: float = 0.10         # P(wrong | known)
    guess: float = 0.20        # P(right | not known)

    def validated(self):
        for name in ("prior", "learn", "slip", "guess"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a probability, got {value}")
        if self.slip >= 0.5 or self.guess >= 0.5:
            raise ValueError(
                "slip and guess must be below 0.5; above it the fitted state "
                "is inverted and 'known' predicts failure")
        return self


class BKT:
    """
    Bayesian Knowledge Tracing, fitted per concept by expectation over the
    observed sequences.

    Deliberately not a neural model and deliberately first. It runs on a
    laptop in milliseconds, which means the pipeline around it — splitting,
    leakage checking, scoring, recording — can be proven correct before any
    of it is used to judge a Transformer.
    """

    name = "BKT"
    checkpoint_suffix = ".json"

    def __init__(self, parameters=None, iterations=20, seed=None):
        self.default = (parameters or BKTParameters()).validated()
        self.iterations = iterations
        self.per_concept = {}
        self.history = []
        # Accepted so the runner can hand every model the run's seed
        # uniformly. This fit is deterministic and uses none of it.
        self.seed = seed
        self._fitted = False

    # ── checkpointing ─────────────────────────────────────────────────

    def save(self, path):
        """
        The fitted parameters, as JSON.

        A LIST of {concept, parameters} rather than an object keyed by
        concept: JSON object keys are always strings, so a dict would turn
        integer concept ids into strings on the way back and quietly score
        every interaction against the default prior.
        """
        import json
        import pathlib

        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "model": self.name,
            "default": dataclasses_asdict(self.default),
            "per_concept": [
                {"concept": concept,
                 "parameters": dataclasses_asdict(parameters)}
                for concept, parameters in sorted(
                    self.per_concept.items(), key=lambda item: str(item[0]))],
        }, indent=2) + "\n", encoding="utf-8")
        return str(path)

    def load(self, path):
        import json
        import pathlib

        payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        if payload["model"] != self.name:
            raise ValueError(
                f"checkpoint holds {payload['model']!r}, not {self.name!r}")
        self.default = BKTParameters(**payload["default"]).validated()
        self.per_concept = {
            entry["concept"]: BKTParameters(**entry["parameters"]).validated()
            for entry in payload["per_concept"]}
        self._fitted = True
        return self

    # ── fitting ───────────────────────────────────────────────────────

    def fit(self, sequences, validation=None):
        """
        `sequences` maps learner -> [{concept, correct}, ...].

        `validation` is accepted and ignored: BKT has nothing to early-stop
        on. The runner passes a validation set to every model uniformly, and
        a model that quietly took a different argument list would be a
        different experiment.

        A simple EM-flavoured fit: the parameters that maximise agreement
        with the observed accuracy per concept, hill-climbed. It is not the
        most sophisticated BKT fit available; it is one whose behaviour can
        be read off the code, which matters more for a baseline.
        """
        by_concept = {}
        for rows in sequences.values():
            for row in rows:
                by_concept.setdefault(concept_of(row), []).append(
                    bool(row["correct"]))

        for concept, outcomes in by_concept.items():
            self.per_concept[concept] = self._fit_one(outcomes)
        self._fitted = True
        return self

    def _fit_one(self, outcomes):
        if not outcomes:
            return self.default
        observed = sum(outcomes) / len(outcomes)
        best, best_error = self.default, float("inf")
        for prior in (0.1, 0.25, 0.4, 0.6):
            for learn in (0.05, 0.15, 0.3):
                candidate = BKTParameters(prior=prior, learn=learn,
                                          slip=self.default.slip,
                                          guess=self.default.guess)
                predicted = self._expected_accuracy(candidate, len(outcomes))
                error = abs(predicted - observed)
                if error < best_error:
                    best, best_error = candidate, error
        return best

    @staticmethod
    def _expected_accuracy(parameters, opportunities):
        known = parameters.prior
        total = 0.0
        for _ in range(max(1, opportunities)):
            total += (known * (1 - parameters.slip)
                      + (1 - known) * parameters.guess)
            known = known + (1 - known) * parameters.learn
        return total / max(1, opportunities)

    # ── prediction ────────────────────────────────────────────────────

    def predict_sequence(self, rows):
        """
        P(correct) for each interaction, using ONLY earlier ones.

        The posterior update happens AFTER the prediction is recorded, which
        is the whole causal contract: position i is scored on knowledge
        derived from positions < i.
        """
        if not self._fitted:
            raise NotTrainedError("fit() before predict_sequence()")

        state, predictions = {}, []
        for row in rows:
            concept = concept_of(row)
            parameters = self.per_concept.get(concept, self.default)
            known = state.get(concept, parameters.prior)

            predictions.append(known * (1 - parameters.slip)
                               + (1 - known) * parameters.guess)

            # observe, then update — never before
            if bool(row["correct"]):
                numerator = known * (1 - parameters.slip)
                denominator = numerator + (1 - known) * parameters.guess
            else:
                numerator = known * parameters.slip
                denominator = numerator + (1 - known) * (1 - parameters.guess)
            posterior = numerator / denominator if denominator else known
            state[concept] = posterior + (1 - posterior) * parameters.learn
        return predictions


CONSTRUCTORS.update({
    "BKT": BKT,
    "DKT": _build_dkt,
    "Transformer": _build_transformer,
    "Transformer+T": _build_transformer_temporal,
    "Transformer+TG": _build_transformer_gated,
    "TA-GTKT": _build_ta_gtkt,
})


# ═════════════════════════════════════════════════════════════
# Metrics
# ═════════════════════════════════════════════════════════════

def auc(labels, scores):
    """
    ROC AUC by rank, ties averaged. No sklearn: one dependency-free function
    is easier to verify than a version pin.
    """
    pairs = sorted(zip(scores, labels))
    ranks, index = {}, 1
    position = 0
    while position < len(pairs):
        end = position
        while end + 1 < len(pairs) and pairs[end + 1][0] == pairs[position][0]:
            end += 1
        average = (index + (index + end - position)) / 2
        for offset in range(position, end + 1):
            ranks[offset] = average
        index += end - position + 1
        position = end + 1

    positives = sum(1 for _s, label in pairs if label)
    negatives = len(pairs) - positives
    if positives == 0 or negatives == 0:
        return float("nan")
    rank_sum = sum(ranks[i] for i, (_s, label) in enumerate(pairs) if label)
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def accuracy(labels, scores, threshold=0.5):
    if not labels:
        return float("nan")
    correct = sum(1 for label, score in zip(labels, scores)
                  if bool(label) == (score >= threshold))
    return correct / len(labels)


def rmse(labels, scores):
    if not labels:
        return float("nan")
    total = sum((float(bool(label)) - score) ** 2
                for label, score in zip(labels, scores))
    return math.sqrt(total / len(labels))


#: How far a probability may be clamped from 0 or 1 in `log_loss`.
#:
#: Unclamped, a single confident wrong answer scores infinity and one row
#: decides the metric for the whole corpus. Clamping is standard, but the
#: bound is a REPORTED CHOICE rather than a hidden constant: it caps the
#: worst per-row penalty at ~13.8 nats, so two log losses are only comparable
#: if they used the same epsilon.
LOG_LOSS_EPSILON = 1e-6


def log_loss(labels, scores, epsilon=LOG_LOSS_EPSILON):
    """
    Mean binary cross-entropy, in nats.

    The metric that punishes CONFIDENT wrongness, which AUC cannot see at all
    — AUC is invariant to any monotone rescaling of the scores, so a model
    that ranks well while being wildly overconfident looks identical to a
    calibrated one. For deciding what to show a learner, the calibration is
    the part that matters.
    """
    if not labels:
        return float("nan")
    total = 0.0
    for label, score in zip(labels, scores):
        clamped = min(max(float(score), epsilon), 1.0 - epsilon)
        total -= (math.log(clamped) if bool(label)
                  else math.log(1.0 - clamped))
    return total / len(labels)
