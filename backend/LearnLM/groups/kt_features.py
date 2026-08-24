"""
KT feature inventory (M2 P2.10a).

What LearnLM can actually feed a knowledge-tracing model, classified by
availability rather than by wishfulness. Pure data — no ORM, no imports from
the model layer — so it can be read by a report, a test, or a future dataset
builder without pulling Django in.

Five classes:

    AVAILABLE   a column exists and means what the model needs it to mean
    DERIVABLE   computable from existing rows by a pure, causal function
    MISSING     not recorded anywhere; needs instrumentation to exist
    UNSAFE      obtainable, but using it would leak or mislead
    DEFERRED    belongs to a later phase; not a gap in this one

The UNSAFE class is the one that earns its place. Two features here are
available and would be actively harmful, and a plain available/missing split
would have marked both as ready to use.
"""

from dataclasses import asdict, dataclass

AVAILABLE = "AVAILABLE"
DERIVABLE = "DERIVABLE"
MISSING = "MISSING"
UNSAFE = "UNSAFE"
DEFERRED = "DEFERRED"


@dataclass(frozen=True)
class Feature:
    name: str
    status: str
    source: str
    verdict: str          # include / exclude / instrument / later
    note: str

    def as_dict(self):
        return asdict(self)


FEATURES = (
    # ── Identity ────────────────────────────────────────────────────────
    Feature("question_id", AVAILABLE, "CodeSubmission.question_id", "include",
            "~1,100 questions. Use a Rasch-style factorisation (topic embedding "
            "+ difficulty scalar x variation vector, per AKT) rather than free "
            "per-question embeddings, which would need far more data than "
            "exists to fit."),

    Feature("topic_id", AVAILABLE, "Question.topic_id", "include",
            "The concept axis. Coarser than the per-skill tagging most KT "
            "datasets carry, which argues for topic-level tracing first."),

    Feature("prerequisite_relationships", AVAILABLE,
            "TopicPrerequisite (NetworkX DAG, cycle-checked in clean())",
            "include",
            "A genuine asset: most public KT datasets have no curriculum "
            "graph. Usable as a structural prior on concept transfer."),

    # ── Outcome ─────────────────────────────────────────────────────────
    Feature("outcome_binary", AVAILABLE, "CodeSubmission.status", "include",
            "accepted / wrong_answer only, per P2.8b's LEARNER_EVIDENCE_"
            "STATUSES. The prediction target."),

    Feature("outcome_class_5valued", AVAILABLE, "CodeSubmission.status",
            "exclude (log only)",
            "compile_error / time_limit / runtime_error are excluded from "
            "evidence by P2.8b because they conflate not-knowing with "
            "mistyping. Reusing that decision rather than minting a second "
            "definition of evidence. Logged for diagnostics."),

    # ── Sequence and time ───────────────────────────────────────────────
    Feature("attempt_number", DERIVABLE,
            "COUNT of prior submissions for (user, question)", "include",
            "MUST count PRIOR attempts only. A total count leaks how many "
            "attempts follow — checked by kt_leakage.audit_causality."),

    Feature("lag_seconds", DERIVABLE,
            "submitted_at delta to the learner's previous interaction",
            "include",
            "SAINT+ reports lag time as a strong temporal feature, and this is "
            "the half of its temporal pair that LearnLM can actually compute."),

    Feature("learner_deliberation_time", MISSING, "-- not recorded --",
            "instrument",
            "**MUST-HAVE GAP.** SAINT+'s 'elapsed time' — how long the learner "
            "thought — is not recorded anywhere. Capturing it needs a client-"
            "side timer from question-render to submit, which is a frontend "
            "change and belongs to P2.10b at the earliest."),

    Feature("execution_time_ms", AVAILABLE, "CodeSubmission.execution_time_ms",
            "EXCLUDE — UNSAFE AS A PROXY",
            "This is the PROGRAM's runtime on Judge0, not the learner's "
            "thinking time. Substituting it for deliberation time would feed "
            "the model a measure of algorithmic efficiency labelled as "
            "cognitive effort — so a fast O(n) solution and a slow O(n^2) one "
            "would read as different learner states for identical knowledge. "
            "It is also confounded by Judge0 queue load."),

    Feature("session_id", DERIVABLE,
            "gap-thresholding on submitted_at", "later",
            "No session concept exists. A 30-minute-gap heuristic is standard "
            "but approximate; not worth the ambiguity until sequences exist."),

    # ── Item metadata ───────────────────────────────────────────────────
    Feature("base_difficulty", AVAILABLE, "Question.base_difficulty",
            "include as PRIOR only",
            "From a three-valued CSV label (Easy/Medium/Hard -> "
            "1000/1300/1600) that no code has ever updated from an outcome. "
            "Usable to initialise a Rasch difficulty scalar; not usable as a "
            "calibrated value."),

    Feature("language", AVAILABLE, "CodeSubmission.language", "include (low-dim)",
            "Language switching is signal, but 5 languages x 1,100 questions "
            "multiplies sparsity. A small embedding, not a partition."),

    # ── Glicko ──────────────────────────────────────────────────────────
    Feature("glicko_rating_live", AVAILABLE, "LearnerTopicSkill.rating",
            "selector input, NOT model input",
            "Feeding it to the Transformer invites degeneracy (the cheapest "
            "loss reduction is to copy it) and train/serve skew. Used at the "
            "selector as a parallel signal instead."),

    Feature("glicko_rd_live", AVAILABLE, "LearnerTopicSkill.rating_deviation",
            "selector input — the confidence gate",
            "The uncertainty measure the Transformer does not have. Gates how "
            "much weight the KT signal receives, which is what makes cold "
            "start fall back automatically."),

    # ── Point-in-time Glicko (M2 P2.9b) ─────────────────────────────────
    #
    # These moved MISSING -> AVAILABLE-GOING-FORWARD when `GlickoSnapshot`
    # started recording. They remain MISSING for every interaction that
    # predates it, and that gap is permanent: `glicko.rate` consumes
    # `periods_inactive` derived from the wall clock at update time, which was
    # never recorded, so replay reconstructs a plausible history rather than
    # the actual one.

    Feature("glicko_rating_before", AVAILABLE,
            "GlickoSnapshot.learner_rating_before "
            "(interactions after M2 P2.9b only)", "include",
            "The learner's rating as it stood immediately BEFORE this "
            "interaction — the exact value fed to glicko.rate. Admissible as "
            "a KT feature; enforced by glicko_history.KT_ADMISSIBLE_FIELDS."),

    Feature("glicko_rd_before", AVAILABLE,
            "GlickoSnapshot.learner_rd_before", "include",
            "Point-in-time uncertainty. The confidence gate the Transformer "
            "has no equivalent of, and the reason cold start can fall back "
            "automatically."),

    Feature("glicko_period_before", AVAILABLE,
            "GlickoSnapshot.learner_periods_inactive", "include",
            "Fractional rating periods of inactivity applied by the update. "
            "Doubles as a legitimate recency feature."),

    Feature("question_glicko_rating_before", AVAILABLE,
            "GlickoSnapshot.question_rating_before", "include",
            "Point-in-time ITEM difficulty — strictly better than the static "
            "base_difficulty label, and exactly the quantity AKT's Rasch "
            "embedding needs."),

    Feature("glicko_rating_after", AVAILABLE, "GlickoSnapshot.*_after",
            "EXCLUDE — UNSAFE, ENCODES THE LABEL",
            "A rating that rose means the learner was correct. Supplying it "
            "while predicting that same interaction hands the model its own "
            "answer. Stored for auditing and gap detection only; "
            "glicko_history.kt_features raises PostInteractionLeakage rather "
            "than returning it, and there is no flag that relaxes that."),

    Feature("glicko_rating_at_interaction_time_historical", MISSING,
            "-- not recorded before M2 P2.9b --", "unrecoverable",
            "Interactions predating the snapshot table have NO point-in-time "
            "Glicko state and never will. Treated as missing, never imputed."),

    # ── Selection context ───────────────────────────────────────────────
    Feature("recommendation_timestamp", AVAILABLE,
            "RecommendationLog.created_at", "include (evaluation)",
            "Useful for measuring decision-to-attempt latency."),

    Feature("chosen_question", AVAILABLE, "RecommendationLog.problem_id",
            "include (evaluation)",
            "CharField, not an FK — joining to Question is string-based and "
            "should be validated before being relied on."),

    Feature("candidate_set", MISSING, "-- only the winner is logged --",
            "instrument",
            "**SHOULD-HAVE.** RecommendationLog records the chosen question "
            "but not the alternatives it beat. Without the candidate set, "
            "counterfactual/off-policy evaluation of a new ranker is not "
            "possible — only online A/B is."),

    Feature("exposure_history", DERIVABLE,
            "COUNT over CodeSubmission per (user, question)", "include",
            "P2.8a already computes attempt_count and last_attempt_at for "
            "routing; the same derivation serves the dataset."),

    # ── Not yet built ───────────────────────────────────────────────────
    Feature("hint_usage", DEFERRED, "-- hint ladder is P7.2 --", "later",
            "Not a P2.10 gap; the feature does not exist in the product yet."),

    Feature("partial_credit", DEFERRED,
            "-- P2.8 mentions it; grading is still pass/fail --", "later",
            "Per-test-case pass counts would give a graded outcome instead of "
            "a boolean, which is strictly more information. Blocked on the "
            "grading pipeline, not on KT."),
)


def by_status():
    """Features grouped by availability class."""
    grouped = {}
    for feature in FEATURES:
        grouped.setdefault(feature.status, []).append(feature)
    return grouped


def must_have_gaps():
    """
    The instrumentation without which the eventual dataset is compromised.

    Distinct from MISSING: a missing feature the model can live without is a
    limitation, whereas these change what the dataset can support at all.
    """
    return [f for f in FEATURES
            if f.status is MISSING and f.verdict.startswith("instrument")]


def unsafe_features():
    """Available, and actively harmful if used. The class that earns its keep."""
    return [f for f in FEATURES if "UNSAFE" in f.verdict or f.status == UNSAFE]


def as_dict():
    """Machine-readable inventory."""
    return {
        "features": [f.as_dict() for f in FEATURES],
        "counts": {status: len(items) for status, items in by_status().items()},
        "must_have_gaps": [f.name for f in must_have_gaps()],
    }
