"""
Knowledge-tracing data readiness (M2 P2.10a).

Answers one question, honestly and reproducibly:

    "How many interactions are actually eligible for Transformer Knowledge
     Tracing training?"

── Why this exists before any model ────────────────────────────────────────

The number that matters is not `CodeSubmission.objects.count()`. It is the
count that survives every trust filter, and the two differ by orders of
magnitude in a system where nothing has yet been oracle-verified. A phase that
began by training on the larger number would be fitting a Transformer to
verdicts produced by answer keys nobody has checked — the precise inversion the
P2.7 milestone exists to prevent.

So the deliverable of P2.10a is a NUMBER and the contract that produced it, not
a model. If that number says "not ready", that is a successful outcome.

── Read-only, unconditionally ──────────────────────────────────────────────

Nothing in this module writes. No save, no update, no create, no delete — not
to grading truth, not to learner state, not to a projection table. A structural
test asserts it rather than trusting this paragraph.

── The projection is derived, not stored ───────────────────────────────────

P2.10a deliberately creates NO new table. Every field the eventual KT dataset
needs is either a column on `CodeSubmission` or derivable from it by a pure
function of rows that already exist, so the projection can be rebuilt from
scratch at any time and is guaranteed to agree with its source. A materialised
table would add a migration, a backfill, and a second thing that can be wrong —
for a consumer that does not exist yet. It becomes justified in P2.10b, when
sequence construction makes repeated full scans expensive.
"""

from dataclasses import asdict, dataclass, field

from django.db.models import Count, Max, Min

from groups.models import CodeSubmission, Question
from groups.services import LEARNER_EVIDENCE_STATUSES

#: Gate verdicts.
NOT_READY = "NOT_READY"
RESEARCH_READY = "RESEARCH_READY"
TRAINING_READY = "TRAINING_READY"


# ═════════════════════════════════════════════════════════════
# The filtering contract
# ═════════════════════════════════════════════════════════════

#: Every filter an interaction must survive, in order, with the reason.
#:
#: Published as data rather than buried in a queryset so the report can print
#: it. The failure mode this guards against is a future reader seeing "1,204
#: interactions" and not knowing which 1,204.
FILTER_CONTRACT = (
    ("adaptive_eligible",
     "adaptive_eligible IS TRUE",
     "P2.7c: the submission was graded against a question whose answer key was "
     "oracle-verified AT THE TIME. Frozen at write; never recomputed."),

    ("has_question",
     "question_id IS NOT NULL",
     "CodeSubmission.question is nullable, so orphaned rows exist. An "
     "interaction with no item cannot be traced."),

    ("has_topic",
     "question.topic_id IS NOT NULL",
     "Concept identity is the axis knowledge tracing traces. A question with "
     "no topic contributes an outcome attributable to nothing."),

    ("evaluable_outcome",
     f"status IN {tuple(sorted(LEARNER_EVIDENCE_STATUSES))}",
     "P2.8b: only `accepted` and `wrong_answer` are evidence ABOUT THE "
     "LEARNER. compile_error, time_limit and runtime_error conflate not "
     "knowing with mistyping, and were excluded from the learner signal by an "
     "earlier phase. Reused here rather than re-decided — a second definition "
     "of 'evidence' would eventually disagree with the first."),
)


def eligible_interactions():
    """
    The queryset every count in this module derives from.

    ONE definition. Each metric filters or aggregates this; none rebuilds it,
    so a change to the contract cannot apply to some numbers and not others.

    ── Two of these four filters are currently redundant ───────────────────

    Found by mutation testing, and kept deliberately:

      * `question__topic__isnull=False` can never exclude a row today, because
        `Question.topic` is NOT NULL at the database level. It is a **proven
        equivalent mutant** — removing it changes no result on any reachable
        database state.
      * `question__isnull=False` is subsumed by it: spanning the join to
        `question__topic` already forces an inner join that drops NULL
        question_id.

    They stay because each states a distinct requirement that a future schema
    change could make load-bearing, and because the report PRINTS this contract
    to operators — a filter chain that quietly relies on a NOT NULL constraint
    elsewhere is harder to audit than one that says what it needs. Since
    behaviour cannot distinguish them, `test_filter_contract_matches_the_sql`
    pins them structurally instead.
    """
    return (CodeSubmission.objects
            .filter(adaptive_eligible=True,
                    question__isnull=False,
                    question__topic__isnull=False,
                    status__in=LEARNER_EVIDENCE_STATUSES))


def all_interactions():
    """Every submission, trusted or not. The denominator, never the dataset."""
    return CodeSubmission.objects.all()


# ═════════════════════════════════════════════════════════════
# Thresholds — proposed, not established
# ═════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ReadinessThresholds:
    """
    PROPOSED research thresholds. **Not scientific truths.**

    They are order-of-magnitude arguments from the KT literature's own
    experimental settings — SAKT/AKT report on datasets of 10^5–10^6
    interactions, SAINT on ~10^8 — and from the observation that attention
    models degrade toward item-difficulty priors when sequences are short.

    They have NOT been validated on LearnLM data, because there is no LearnLM
    data to validate them on. That circularity is real and is the reason they
    are configurable: the correct value is an empirical question that P2.10c/d
    will answer by training baselines at several data volumes.

    Treat a threshold breach as "the evidence does not yet support proceeding",
    never as "the model would definitely fail".
    """

    #: RESEARCH_READY — enough to fit baselines and measure whether a
    #: sequence model beats item difficulty at all.
    research_interactions: int = 5_000
    research_learners: int = 50
    research_depth: int = 10           # interactions per learner
    research_questions: int = 50
    research_topics: int = 5
    research_days: int = 30

    #: TRAINING_READY — enough to fit a Transformer with a defensible
    #: temporal split and cold-start slices.
    training_interactions: int = 50_000
    training_learners: int = 500
    training_depth: int = 20
    training_questions: int = 200
    training_topics: int = 10
    training_days: int = 90

    #: Both tiers: the minority outcome class must not be vanishing, or AUC
    #: becomes uninformative and calibration untrainable.
    min_minority_outcome_rate: float = 0.10

    #: Learners below this depth are the cold-start slice. Evaluation needs
    #: enough of them to measure cold-start behaviour separately.
    cold_start_depth: int = 5
    min_cold_start_learners: int = 20


# ═════════════════════════════════════════════════════════════
# Census
# ═════════════════════════════════════════════════════════════

@dataclass
class Census:
    """Every count P2.10a reports. Pure data; no ORM handles retained."""

    # Volume
    total_interactions: int = 0
    eligible_interactions: int = 0
    ineligible_interactions: int = 0
    eligible_percentage: float = 0.0

    # Breadth
    eligible_learners: int = 0
    eligible_questions: int = 0
    eligible_topics: int = 0

    # Depth
    learners_ge_20: int = 0
    learners_ge_50: int = 0
    learners_ge_100: int = 0
    cold_start_learners: int = 0
    median_depth: float = 0.0
    max_depth: int = 0

    # Distributions
    outcome_distribution: dict = field(default_factory=dict)
    language_distribution: dict = field(default_factory=dict)
    per_topic_counts: dict = field(default_factory=dict)
    depth_histogram: dict = field(default_factory=dict)
    minority_outcome_rate: float = 0.0

    # Per-question coverage
    questions_ge_5_attempts: int = 0
    max_question_attempts: int = 0

    # Temporal
    earliest: object = None
    latest: object = None
    span_days: int = 0

    # Trust
    oracle_verified_questions: int = 0
    total_questions: int = 0
    questions_without_trustworthy_evidence: int = 0

    def as_dict(self):
        data = asdict(self)
        data["earliest"] = self.earliest.isoformat() if self.earliest else None
        data["latest"] = self.latest.isoformat() if self.latest else None
        return data


def collect_census():
    """
    Every metric, from the database, writing nothing.

    Deterministic: identical database state produces an identical Census, so
    two runs can be diffed to see what changed rather than whether the
    measurement drifted.
    """
    census = Census()
    eligible = eligible_interactions()

    census.total_interactions = all_interactions().count()
    census.eligible_interactions = eligible.count()
    census.ineligible_interactions = (
        census.total_interactions - census.eligible_interactions)
    census.eligible_percentage = _percent(
        census.eligible_interactions, census.total_interactions)

    census.total_questions = Question.objects.count()
    census.oracle_verified_questions = Question.objects.filter(
        trust_state=Question.TRUST_ORACLE_VERIFIED).count()
    census.questions_without_trustworthy_evidence = (
        census.total_questions - census.oracle_verified_questions)

    if not census.eligible_interactions:
        # Everything below describes a dataset. There isn't one; returning
        # zeros beats returning statistics computed over an empty set, which
        # read as though something was measured.
        return census

    census.eligible_learners = eligible.values("user_id").distinct().count()
    census.eligible_questions = eligible.values("question_id").distinct().count()
    census.eligible_topics = (
        eligible.values("question__topic_id").distinct().count())

    depths = list(eligible.values("user_id")
                  .annotate(n=Count("id"))
                  .values_list("n", flat=True))
    census.learners_ge_20 = sum(1 for n in depths if n >= 20)
    census.learners_ge_50 = sum(1 for n in depths if n >= 50)
    census.learners_ge_100 = sum(1 for n in depths if n >= 100)
    census.cold_start_learners = sum(1 for n in depths if n < 5)
    census.median_depth = _median(depths)
    census.max_depth = max(depths)
    census.depth_histogram = _bucket_depths(depths)

    census.outcome_distribution = _tally(eligible, "status")
    census.language_distribution = _tally(eligible, "language")
    census.per_topic_counts = _tally(eligible, "question__topic__name")

    accepted = census.outcome_distribution.get("accepted", 0)
    wrong = census.outcome_distribution.get("wrong_answer", 0)
    census.minority_outcome_rate = _percent(
        min(accepted, wrong), accepted + wrong) / 100.0

    question_counts = list(eligible.values("question_id")
                           .annotate(n=Count("id"))
                           .values_list("n", flat=True))
    census.questions_ge_5_attempts = sum(1 for n in question_counts if n >= 5)
    census.max_question_attempts = max(question_counts)

    bounds = eligible.aggregate(first=Min("submitted_at"),
                                last=Max("submitted_at"))
    census.earliest, census.latest = bounds["first"], bounds["last"]
    census.span_days = (census.latest - census.earliest).days

    return census


def _tally(queryset, field_name):
    return {
        str(row[field_name]): row["n"]
        for row in (queryset.values(field_name)
                    .annotate(n=Count("id"))
                    .order_by("-n"))
    }


def _bucket_depths(depths):
    buckets = {"1-4": 0, "5-19": 0, "20-49": 0, "50-99": 0, "100+": 0}
    for depth in depths:
        if depth < 5:
            buckets["1-4"] += 1
        elif depth < 20:
            buckets["5-19"] += 1
        elif depth < 50:
            buckets["20-49"] += 1
        elif depth < 100:
            buckets["50-99"] += 1
        else:
            buckets["100+"] += 1
    return buckets


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _percent(part, whole):
    return round(100.0 * part / whole, 4) if whole else 0.0


# ═════════════════════════════════════════════════════════════
# The gate
# ═════════════════════════════════════════════════════════════

@dataclass
class GateResult:
    verdict: str
    reasons: list = field(default_factory=list)
    satisfied: list = field(default_factory=list)
    thresholds: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


def evaluate_gate(census, thresholds=None, leakage=None):
    """
    NOT_READY / RESEARCH_READY / TRAINING_READY, with reasons for each.

    Multi-dimensional on purpose. "Enough rows" is the wrong question: 50,000
    interactions from three learners is not a knowledge-tracing dataset, it is
    three learning curves. Breadth, depth, temporal span, outcome balance and
    leakage-safety each gate independently, and the verdict is the weakest.
    """
    thresholds = thresholds or ReadinessThresholds()
    result = GateResult(verdict=NOT_READY, thresholds=asdict(thresholds))

    def check(label, actual, required, tier):
        met = actual >= required
        line = f"{label}: {actual} (need {required} for {tier})"
        (result.satisfied if met else result.reasons).append(line)
        return met

    # ── Trust gate. Nothing else matters if no label is trustworthy. ──
    if census.oracle_verified_questions == 0:
        result.reasons.append(
            "no question has reached ORACLE_VERIFIED, so no submission can be "
            "adaptive_eligible and there are zero trustworthy labels")
    if census.eligible_interactions == 0:
        result.reasons.append(
            "zero eligible interactions — there is no dataset to assess")
        result.verdict = NOT_READY
        return result

    # ── Leakage safety is a hard gate at BOTH tiers. ──
    if leakage is not None and not leakage.is_safe:
        result.reasons.extend(
            f"leakage: {problem}" for problem in leakage.problems)

    research = all([
        check("eligible interactions", census.eligible_interactions,
              thresholds.research_interactions, RESEARCH_READY),
        check("eligible learners", census.eligible_learners,
              thresholds.research_learners, RESEARCH_READY),
        check("learners at depth", census.learners_ge_20,
              1, RESEARCH_READY),
        check("eligible questions", census.eligible_questions,
              thresholds.research_questions, RESEARCH_READY),
        check("eligible topics", census.eligible_topics,
              thresholds.research_topics, RESEARCH_READY),
        check("temporal span (days)", census.span_days,
              thresholds.research_days, RESEARCH_READY),
    ])

    if census.minority_outcome_rate < thresholds.min_minority_outcome_rate:
        result.reasons.append(
            f"minority outcome rate {census.minority_outcome_rate:.3f} < "
            f"{thresholds.min_minority_outcome_rate} — AUC is uninformative "
            f"and calibration untrainable on a near-constant label")
        research = False

    if leakage is not None and not leakage.is_safe:
        research = False

    if not research:
        result.verdict = NOT_READY
        return result

    training = all([
        check("eligible interactions", census.eligible_interactions,
              thresholds.training_interactions, TRAINING_READY),
        check("eligible learners", census.eligible_learners,
              thresholds.training_learners, TRAINING_READY),
        check(f"learners with >= {thresholds.training_depth} interactions",
              census.learners_ge_20, thresholds.training_learners // 2,
              TRAINING_READY),
        check("eligible questions", census.eligible_questions,
              thresholds.training_questions, TRAINING_READY),
        check("eligible topics", census.eligible_topics,
              thresholds.training_topics, TRAINING_READY),
        check("temporal span (days)", census.span_days,
              thresholds.training_days, TRAINING_READY),
        check("cold-start learners (for the cold-start eval slice)",
              census.cold_start_learners, thresholds.min_cold_start_learners,
              TRAINING_READY),
    ])

    result.verdict = TRAINING_READY if training else RESEARCH_READY
    return result
