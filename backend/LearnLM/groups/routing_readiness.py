"""
Routing-evaluation data readiness (M2 P2.30).

READ-ONLY. Every function here reads; none writes, and a structural test
asserts the module imports nothing that can reach a write path.

── The question this answers ───────────────────────────────────────────────

`retrain_ai` refuses to fit the routing classifier below 100 outcome labels.
That threshold is the repository's, not this module's — it is read from
`retrain_ai` rather than restated, so the two cannot drift apart.

But "how many labels do we have" is the wrong question on its own, and
answering only that is how a contaminated dataset gets trained on. A
`RecommendationLog` row carries an outcome the moment
`actual_result_correct` is non-null; whether that outcome is TRUSTWORTHY is a
separate fact, and today the two diverge completely.

── Why a label can exist and still be unusable ─────────────────────────────

Outcome-closing is gated at write time. `services.py` closes a recommendation
only `if adaptive_eligible` (M2 P2.7c), so a verdict from a question no oracle
has checked cannot become a training label. That gate is correct and is not
in question here.

The gate is a WRITE-time gate, and it was added part-way through the
project's life. Rows written before it carry no such guarantee, and nothing
on the row records which regime produced it. `retrain_ai` selects labels with
`actual_result_correct__isnull=False` and no trust condition — its FEATURES
are trust-filtered, its LABELS are not, because the label filter was assumed
to have happened upstream.

So a label is counted as trustworthy here only when it can be SHOWN to be:
when an adaptive-eligible submission by the same learner, for the same
problem, exists at or after the decision. That is the same (user, problem,
time) join the flywheel itself uses to close an outcome. It is deliberately
conservative — it can undercount a legitimate label whose submission row was
deleted — and undercounting is the correct direction for a gate that decides
whether a model may be trained.

── What this module refuses to do ──────────────────────────────────────────

It does not repair, delete, or relabel anything. Contaminated labels are
reported, not cleaned: deciding what to do about them is an operator's call
with an audit trail, not a side effect of running a report.
"""

from dataclasses import asdict, dataclass, field

from django.db.models import Count, Max, Min

from groups.models import CodeSubmission, Question, RecommendationLog

#: Gate verdicts.
NOT_READY = "NOT_READY"
EVALUATION_READY = "EVALUATION_READY"
TRAINING_READY = "TRAINING_READY"

#: Serve-time cold-start telemetry: `compute_routing_telemetry` returns
#: (0.7, 0.0, 0) when a learner has no adaptive-eligible submissions. A
#: decision made in that state is a cold-start decision, and the count matters
#: because a dataset that is entirely cold-start carries no learner signal at
#: all — every row would share the same two features.
COLD_START_SAMPLE_SIZE = 0


def rf_min_labels():
    """
    The training threshold, read from `retrain_ai` rather than restated.

    Duplicating `100` here would create a second definition that a future
    change to the first would silently leave behind — the report would then
    say READY while the command that actually trains refused. Read from the
    source so there is exactly one.
    """
    from groups.management.commands import retrain_ai
    return retrain_ai.MIN_TRAINING_LABELS


# ═════════════════════════════════════════════════════════════
# The label contract
# ═════════════════════════════════════════════════════════════

#: What a decision→outcome pair must satisfy to count as usable, with why.
#:
#: Published as data so the report can print it. The failure this guards
#: against is a reader seeing "35 labels" and not knowing which 35.
LABEL_CONTRACT = (
    ("has_outcome",
     "actual_result_correct IS NOT NULL",
     "An open recommendation has no outcome yet. It is a decision, not a "
     "decision→outcome pair, and cannot train or evaluate anything."),

    ("has_problem",
     "problem_id IS NOT NULL AND problem_id <> ''",
     "RecommendationLog.problem_id is nullable. A decision with no item "
     "cannot be joined to the submission that resolved it."),

    ("trust_backed",
     "an adaptive_eligible CodeSubmission exists for (user, problem) at or "
     "after the decision",
     "P2.7c gates outcome-closing at WRITE time, so rows written before that "
     "gate carry no trust guarantee and nothing on the row records which "
     "regime produced it. Requiring a demonstrable eligible submission is the "
     "same join the flywheel uses to close an outcome, and it fails closed."),
)


def all_decisions():
    """Every routing decision ever logged. The denominator, never the dataset."""
    return RecommendationLog.objects.all()


def closed_decisions():
    """
    Decisions carrying an outcome — what `retrain_ai` would train on TODAY.

    Deliberately NOT the usable set. This is the number the training command
    counts, reproduced exactly so the report can show the gap between what
    would be trained on and what is defensible.
    """
    return RecommendationLog.objects.filter(actual_result_correct__isnull=False)


def _eligible_solve_times():
    """
    {(user_id, question_id): [submitted_at, ...]} over adaptive-eligible rows.

    Fetched once. The earlier shape of this module ran one `.exists()` per
    decision inside a `.iterator()` loop, which holds a server-side cursor
    open while issuing further queries — under a connection pool that
    deadlocks, and it did. One query, matched in memory, is both correct and
    the only version that survives a pool.
    """
    times = {}
    rows = (CodeSubmission.objects
            .filter(adaptive_eligible=True, question__isnull=False)
            .values_list("user_id", "question_id", "submitted_at"))
    for user_id, question_id, submitted_at in rows:
        times.setdefault((user_id, question_id), []).append(submitted_at)
    return times


def trustworthy_decisions():
    """
    Closed decisions that can be SHOWN to rest on verified grading truth.

    The flywheel finds the latest open decision for a submission; this asks
    the mirror question — does any eligible submission exist that could have
    closed this decision? Same (user, problem, time) join, run in reverse.
    """
    solves = _eligible_solve_times()
    keep = []
    for pk, user_id, problem_id, created_at in closed_decisions().values_list(
            "pk", "user_id", "problem_id", "created_at"):
        question_id = _as_question_id(problem_id)
        if question_id is None:
            continue
        if any(when >= created_at
               for when in solves.get((user_id, question_id), ())):
            keep.append(pk)
    return RecommendationLog.objects.filter(pk__in=keep)


def _as_question_id(problem_id):
    """
    `problem_id` is a CharField with no foreign key, so it can hold anything —
    a stale id, a slug, an empty string. None means "not joinable".
    """
    if not problem_id:
        return None
    try:
        return int(problem_id)
    except (TypeError, ValueError):
        return None


def contaminated_decisions():
    """
    Closed decisions whose trustworthiness cannot be demonstrated.

    Named for what it is. These rows are not necessarily WRONG — a label
    whose submission row was later deleted lands here too — but nothing in
    the database can vouch for them, and a training set cannot be built out
    of rows that cannot be vouched for.
    """
    return closed_decisions().exclude(
        pk__in=trustworthy_decisions().values("pk"))


# ═════════════════════════════════════════════════════════════
# Census
# ═════════════════════════════════════════════════════════════

@dataclass
class RoutingCensus:
    # Content
    questions_total: int = 0
    oracle_verified_questions: int = 0
    servable_questions: int = 0
    trusted_share_of_servable: float = 0.0

    # Interactions
    submissions_total: int = 0
    adaptive_eligible_submissions: int = 0
    learners_with_submissions: int = 0
    learners_with_adaptive_interactions: int = 0
    max_submissions_per_learner: int = 0
    median_submissions_per_learner: float = 0.0

    # Decisions
    decisions_total: int = 0
    decisions_closed: int = 0
    decisions_open: int = 0
    decisions_trustworthy: int = 0
    decisions_contaminated: int = 0

    # Routes
    route_counts: dict = field(default_factory=dict)
    hierarchical: int = 0
    flat: int = 0
    cold_start_decisions: int = 0

    # Attribution
    policy_versions: dict = field(default_factory=dict)
    decisions_without_policy_version: int = 0

    # Outcome balance
    label_positive: int = 0
    label_negative: int = 0
    minority_outcome_rate: float = 0.0

    # Integrity
    decisions_missing_problem_id: int = 0
    decisions_with_unresolvable_problem: int = 0
    span_days: int = 0

    def as_dict(self):
        return asdict(self)


def collect_census():
    """Every number the report prints. Reads only."""
    census = RoutingCensus()

    # ── Content ──────────────────────────────────────────────────────
    from groups.coding_views import _servable_questions

    census.questions_total = Question.objects.count()
    census.oracle_verified_questions = Question.objects.filter(
        status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED).count()
    census.servable_questions = _servable_questions().count()
    census.trusted_share_of_servable = _ratio(
        census.oracle_verified_questions, census.servable_questions)

    # ── Interactions ─────────────────────────────────────────────────
    census.submissions_total = CodeSubmission.objects.count()
    eligible = CodeSubmission.objects.filter(adaptive_eligible=True)
    census.adaptive_eligible_submissions = eligible.count()
    census.learners_with_submissions = (
        CodeSubmission.objects.values("user").distinct().count())
    census.learners_with_adaptive_interactions = (
        eligible.values("user").distinct().count())

    depths = list(eligible.values("user").annotate(n=Count("id"))
                  .values_list("n", flat=True))
    census.max_submissions_per_learner = max(depths) if depths else 0
    census.median_submissions_per_learner = _median(depths)

    # ── Decisions ────────────────────────────────────────────────────
    census.decisions_total = all_decisions().count()
    census.decisions_closed = closed_decisions().count()
    census.decisions_open = census.decisions_total - census.decisions_closed
    census.decisions_trustworthy = trustworthy_decisions().count()
    census.decisions_contaminated = contaminated_decisions().count()

    # ── Routes ───────────────────────────────────────────────────────
    census.route_counts = _tally(all_decisions(), "engine_used")
    census.hierarchical = census.route_counts.get("hierarchical", 0)
    census.flat = census.route_counts.get("flat", 0)
    census.cold_start_decisions = _count_cold_start()

    # ── Attribution ──────────────────────────────────────────────────
    census.policy_versions = _tally(all_decisions(), "policy_version")
    census.decisions_without_policy_version = all_decisions().filter(
        policy_version__isnull=True).count()

    # ── Outcome balance, over the TRUSTWORTHY set only ───────────────
    # Balance of a contaminated set is not a property worth reporting: it
    # describes a dataset that must not be used.
    trustworthy = trustworthy_decisions()
    census.label_positive = trustworthy.filter(
        actual_result_correct=True).count()
    census.label_negative = trustworthy.filter(
        actual_result_correct=False).count()
    total_labels = census.label_positive + census.label_negative
    census.minority_outcome_rate = (
        _ratio(min(census.label_positive, census.label_negative), total_labels)
        if total_labels else 0.0)

    # ── Integrity ────────────────────────────────────────────────────
    census.decisions_missing_problem_id = (
        all_decisions().filter(problem_id__isnull=True).count()
        + all_decisions().filter(problem_id="").count())
    census.decisions_with_unresolvable_problem = _count_unresolvable()

    bounds = all_decisions().aggregate(first=Min("created_at"),
                                       last=Max("created_at"))
    if bounds["first"] and bounds["last"]:
        census.span_days = (bounds["last"] - bounds["first"]).days

    return census


def _count_cold_start():
    """
    Decisions made while the learner had no adaptive-eligible history.

    Mirrors `compute_routing_telemetry`: sample_size 0 is cold start. Counted
    as of the decision, not now, because a learner who later became
    experienced was still cold at the moment they were routed.
    """
    by_learner = {}
    for user_id, submitted_at in (CodeSubmission.objects
                                  .filter(adaptive_eligible=True)
                                  .values_list("user_id", "submitted_at")):
        by_learner.setdefault(user_id, []).append(submitted_at)

    cold = 0
    for user_id, created_at in all_decisions().values_list(
            "user_id", "created_at"):
        prior = sum(1 for when in by_learner.get(user_id, ())
                    if when < created_at)
        if prior == COLD_START_SAMPLE_SIZE:
            cold += 1
    return cold


def _count_unresolvable():
    """
    Decisions whose `problem_id` names no existing question.

    `problem_id` is a CharField with no foreign key, so a deleted or renamed
    question leaves a dangling string. These rows cannot be joined to content
    and must not be silently treated as valid.
    """
    known = set(Question.objects.values_list("id", flat=True))
    unresolvable = 0
    for problem_id in all_decisions().values_list("problem_id", flat=True):
        if not problem_id:
            continue                       # counted as missing, not dangling
        question_id = _as_question_id(problem_id)
        if question_id is None or question_id not in known:
            unresolvable += 1
    return unresolvable


def _tally(queryset, field_name):
    return {
        (str(row[field_name]) if row[field_name] is not None else "(none)"):
            row["n"]
        for row in queryset.values(field_name).annotate(n=Count("id"))
                           .order_by("-n")
    }


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _ratio(part, whole):
    return round(part / whole, 6) if whole else 0.0


# ═════════════════════════════════════════════════════════════
# The gate
# ═════════════════════════════════════════════════════════════

@dataclass
class RoutingGateResult:
    verdict: str
    reasons: list = field(default_factory=list)
    satisfied: list = field(default_factory=list)
    threshold: int = 0

    def as_dict(self):
        return asdict(self)


def evaluate_gate(census, min_labels=None):
    """
    NOT_READY / EVALUATION_READY / TRAINING_READY, with reasons.

    The threshold is `retrain_ai`'s, unchanged. What this adds is that it is
    applied to the TRUSTWORTHY count rather than the closed count — reaching
    100 contaminated labels would satisfy the training command and still be
    the wrong thing to train on.
    """
    threshold = rf_min_labels() if min_labels is None else min_labels
    result = RoutingGateResult(verdict=NOT_READY, threshold=threshold)

    if census.oracle_verified_questions == 0:
        result.reasons.append(
            "no question has reached ORACLE_VERIFIED, so no submission can be "
            "adaptive_eligible and no label can be trustworthy")

    if census.decisions_contaminated:
        result.reasons.append(
            f"{census.decisions_contaminated} of {census.decisions_closed} "
            f"closed decisions cannot be shown to rest on verified grading "
            f"truth; they must not be trained on")

    if census.decisions_trustworthy == 0:
        result.reasons.append(
            "zero trustworthy decision→outcome pairs — there is no dataset to "
            "evaluate, whatever the raw label count says")
        result.verdict = NOT_READY
        return result

    evaluation = census.decisions_trustworthy >= threshold
    line = (f"trustworthy decision→outcome pairs: "
            f"{census.decisions_trustworthy} (need {threshold})")
    (result.satisfied if evaluation else result.reasons).append(line)

    if census.minority_outcome_rate < 0.10:
        result.reasons.append(
            f"minority outcome rate {census.minority_outcome_rate:.3f} < 0.10 "
            f"— AUC is uninformative on a near-constant label")
        evaluation = False

    if census.cold_start_decisions == census.decisions_total:
        result.reasons.append(
            "every decision was made at cold start, so all rows share the "
            "same telemetry features and carry no learner signal")
        evaluation = False

    if not evaluation:
        result.verdict = NOT_READY
        return result

    result.verdict = (TRAINING_READY
                      if census.decisions_trustworthy >= threshold
                      else EVALUATION_READY)
    return result
