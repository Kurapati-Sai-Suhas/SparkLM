"""
Knowledge-tracing research pipeline (M2 P2.11b).

SEPARATE FROM PRODUCTION. Nothing here reads `groups` models, writes a row,
or influences what a learner sees. It exists to compare knowledge-tracing
models on a PUBLIC dataset under one honest split.

Why not SparkLM's own history: there are 44 submissions. That is not a
training set, and a model fitted on it would report numbers about noise.
`kt_dataset/` (the existing package) handles SparkLM's data and its
readiness question; this package handles research corpora.
"""

from kt_research import experiment, models, splits

__all__ = ["splits", "models", "experiment"]
