"""
The model ladder (M2 P2.11b).

Seven rungs, in the order the ablation needs them. Each is registered with
what it consumes, so the comparison is between models that genuinely differ
rather than between names.

    BKT              per-concept, no sequence model      IMPLEMENTED
    DKT              RNN over interactions               NOT IMPLEMENTED
    SAKT             self-attention, key/query on skill  NOT IMPLEMENTED
    AKT              monotonic attention + Rasch         NOT IMPLEMENTED
    Transformer      plain encoder baseline              NOT IMPLEMENTED
    TA-GTKT          + temporal + gating                 NOT IMPLEMENTED
    TA-GTKT-P        + prerequisite signal               NOT IMPLEMENTED

Only BKT is implemented, and that is deliberate rather than a shortfall:
it needs no framework, no GPU and no downloaded corpus, so the pipeline can
be proven end-to-end — split, train, score, record — before any neural work
begins. The rest raise `NotImplementedError` with what they still need. A
stub that silently returned 0.5 would let a broken pipeline report a
plausible AUC, which is the failure this file exists to avoid.

── On the name ─────────────────────────────────────────────────────────────

TA-GTKT is a NAME FOR THIS CONFIGURATION, not a claim of novelty. Temporal
encoding, gated fusion and prerequisite priors all appear in the KT
literature; what this project can honestly claim is a controlled comparison
of them on one dataset with one split. Any stronger claim needs a literature
review that has not been done.
"""

import math
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
    ModelSpec("DKT", False, ("concept", "correctness"),
              "Deep Knowledge Tracing: an RNN over the interaction sequence.",
              needs="a tensor framework and a public dataset"),
    ModelSpec("SAKT", False, ("concept", "correctness"),
              "Self-Attentive KT: attention from past interactions to the "
              "queried skill.",
              needs="a tensor framework and a public dataset"),
    ModelSpec("AKT", False, ("concept", "correctness", "difficulty"),
              "Attentive KT: monotonic attention with Rasch-style "
              "difficulty embeddings.",
              needs="a tensor framework and a public dataset"),
    ModelSpec("Transformer", False, ("concept", "correctness"),
              "Plain encoder baseline — the control the additions are "
              "measured against.",
              needs="a tensor framework and a public dataset"),
    ModelSpec("TA-GTKT", False,
              ("concept", "correctness", "timestamp", "response_time",
               "delta_time", "attempt_count", "difficulty"),
              "Temporal-Aware Gated Transformer KT: embeddings -> temporal "
              "encoding -> gated fusion -> encoder -> next-response "
              "probability.",
              needs="a tensor framework, a public dataset, and the "
                    "Transformer baseline to compare against"),
    ModelSpec("TA-GTKT-P", False,
              ("concept", "correctness", "timestamp", "response_time",
               "delta_time", "attempt_count", "difficulty", "prerequisites"),
              "TA-GTKT plus a prerequisite signal derived from the "
              "curriculum DAG.",
              needs="TA-GTKT, plus the research prerequisite representation"),
)}


def build(name, **parameters):
    spec = SPECS.get(name)
    if spec is None:
        raise KeyError(f"unknown model {name!r}; known: {sorted(SPECS)}")
    if not spec.implemented:
        raise NotImplementedError(
            f"{name} is declared but not implemented. It needs: "
            f"{spec.needs}. Returning a placeholder score here would let a "
            f"broken pipeline report a plausible number.")
    return BKT(**parameters)


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

    def __init__(self, parameters=None, iterations=20):
        self.default = (parameters or BKTParameters()).validated()
        self.iterations = iterations
        self.per_concept = {}
        self._fitted = False

    # ── fitting ───────────────────────────────────────────────────────

    def fit(self, sequences):
        """
        `sequences` maps learner -> [{"concept": c, "correct": bool}, ...].

        A simple EM-flavoured fit: the parameters that maximise agreement
        with the observed accuracy per concept, hill-climbed. It is not the
        most sophisticated BKT fit available; it is one whose behaviour can
        be read off the code, which matters more for a baseline.
        """
        by_concept = {}
        for rows in sequences.values():
            for row in rows:
                by_concept.setdefault(row["concept"], []).append(
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
            concept = row["concept"]
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
