"""
Trusted-content coverage per curriculum topic (M2 P2.32).

READ-ONLY. Nothing here writes, and a structural test asserts it.

── Why this exists ─────────────────────────────────────────────────────────

P2.31 made the recommender prefer trusted questions within a difficulty band.
That policy can only act where trusted content EXISTS: replayed over the real
bank it changed the first pick in exactly the five topics that contain a
verified question, and left the other fifteen untouched. The exposure
mechanism is no longer the constraint. Supply is.

So the question this answers is not "how many questions are published" but
"which topic is next, and what is the one artifact standing in its way".

── Why it cannot advance anything itself ───────────────────────────────────

Every uncovered topic is blocked at the SAME step, and it is the one step no
command may perform:

    reference_create   needs --source-file: an authored answer key
    reference_review   needs a human to move DRAFT -> APPROVED -> ACTIVE
    oracle_execute     automated, but needs an ACTIVE reference
    quality_gate       automated, read-only, needs a spec
    question_approve   needs a named human, a digest and a quality report
    question_promote   the only writer of trust_state, re-proves everything

Steps 1, 2 and 5 are irreducibly human. Measured across all seventeen
uncovered topics: zero reference solutions, zero oracle executions, zero
approvals. There is no candidate anywhere that automation could carry
further, which is why this module reports a WORKLIST and stops.

Fabricating a reference solution would be fabricating grading truth. The
whole content-trust architecture exists to make that impossible, and a report
is not the place to start.

── What the ranking is, and is not ─────────────────────────────────────────

The candidate ordering below is MECHANICAL: servability, whether the
difficulty is reachable under the current exposure policy, how many peers
share that difficulty, curriculum depth, topic size. It knows nothing about
whether a question is well-posed, canonical, or worth an operator's time.

It is a shortlist to review, never a decision. The operator picks.
"""

from dataclasses import asdict, dataclass, field

from django.db.models import Count

from groups.models import (
    OracleExecution, Question, QuestionApproval, ReferenceSolution, Topic,
    TopicPrerequisite, UserCodingProfile,
)

#: The one artifact every uncovered topic is missing, named once.
BLOCKING_ARTIFACT = "reference solution (operator-authored)"

#: The pipeline, with who may perform each step. Published as data so the
#: report can print it rather than a reader having to trust prose.
TRUST_PIPELINE = (
    ("reference_create", "OPERATOR",
     "Authors the answer key. Takes --source-file, never --source: a "
     "reference solution passed as an argument lands in shell history."),
    ("reference_review", "HUMAN REVIEW",
     "DRAFT -> IN_REVIEW -> APPROVED -> ACTIVE. Approval provenance is frozen "
     "at the moment a named human vouches for a named text."),
    ("oracle_execute", "AUTOMATED",
     "Runs the ACTIVE reference against the hidden cases. Cannot write "
     "expected_output, status, trust_state or adaptive_eligible."),
    ("quality_gate", "AUTOMATED",
     "Mutation testing on Judge0. Read-only; emits the JSON report that "
     "approval requires."),
    ("question_approve", "HUMAN REVIEW",
     "Append-only. Requires the digest from question_review plus the quality "
     "report. Records judgement; never sets trust_state."),
    ("question_promote", "OPERATOR",
     "The only writer of trust_state. Rebuilds every fact from live state "
     "and refuses if anything has moved since approval."),
)


@dataclass
class TopicCoverage:
    name: str
    depth: int = 0
    questions: int = 0
    servable: int = 0
    trusted: int = 0
    reference_solutions: int = 0
    oracle_executions: int = 0
    approvals: int = 0
    unlocks: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    blocker: str = ""

    def as_dict(self):
        return asdict(self)


def _prerequisite_edges():
    edges = {}
    for edge in TopicPrerequisite.objects.select_related(
            "topic", "prerequisite"):
        edges.setdefault(edge.topic.name, set()).add(edge.prerequisite.name)
    return edges


def _depth(topic_name, edges, seen=None):
    """
    How many prerequisite hops before this topic unlocks.

    `seen` guards a cycle. The DAG should not contain one, but a report that
    recurses forever on bad data is worse than one that reports a zero.
    """
    seen = seen or set()
    if topic_name in seen or topic_name not in edges:
        return 0
    return 1 + max(
        (_depth(prereq, edges, seen | {topic_name})
         for prereq in edges[topic_name]),
        default=0)


def _unlocks(edges):
    """topic -> the topics it is a prerequisite FOR."""
    forward = {}
    for topic_name, prereqs in edges.items():
        for prereq in prereqs:
            forward.setdefault(prereq, []).append(topic_name)
    return forward


def median_learner_elo():
    """
    The Elo the exposure policy will actually be evaluated at.

    Read from live profiles rather than assumed: the default is 1200 and 17 of
    20 production learners sit exactly there, but a report that hard-coded it
    would quietly go wrong the moment that stopped being true.
    """
    ratings = sorted(UserCodingProfile.objects.values_list(
        "elo_rating", flat=True))
    if not ratings:
        return UserCodingProfile._meta.get_field("elo_rating").default
    middle = len(ratings) // 2
    if len(ratings) % 2:
        return float(ratings[middle])
    return (ratings[middle - 1] + ratings[middle]) / 2.0


def _reachable(difficulty, target_elo):
    """
    Whether the exposure policy would put this difficulty in the TOP band.

    Imported from `coding_views` rather than restated: a second copy of the
    band rule would eventually disagree with the one that actually serves.
    """
    from groups.coding_views import EXPOSURE_ELO_BAND

    return abs(float(difficulty) - target_elo) < EXPOSURE_ELO_BAND


def rank_candidates(topic_name, target_elo, limit=3):
    """
    A mechanical shortlist for one topic. Suggestions, never decisions.

    Ordering:
      1. reachable at the median learner Elo   — an unreachable trusted
         question is verified content nobody is offered
      2. peers at that difficulty, descending  — a question competing with
         many others makes the preference visible
      3. question id                           — terminal, so the shortlist
         is reproducible
    """
    from groups.coding_views import _servable_questions

    peers = dict(
        _servable_questions().filter(topic__name=topic_name)
        .values_list("base_difficulty")
        .annotate(n=Count("id")))

    rows = list(_servable_questions().filter(topic__name=topic_name)
                .values("id", "title", "base_difficulty"))
    rows.sort(key=lambda r: (
        not _reachable(r["base_difficulty"], target_elo),
        -peers.get(r["base_difficulty"], 0),
        r["id"],
    ))
    return [{
        "id": row["id"],
        "title": (row["title"] or "")[:44],
        "difficulty": float(row["base_difficulty"]),
        "reachable": _reachable(row["base_difficulty"], target_elo),
    } for row in rows[:limit]]


def collect(include_covered=False, limit=3):
    """Coverage for every topic. Reads only."""
    from groups.coding_views import _servable_questions

    edges = _prerequisite_edges()
    forward = _unlocks(edges)
    target_elo = median_learner_elo()

    reference_ids = set(ReferenceSolution.objects.values_list(
        "question_id", flat=True))
    oracle_ids = set(OracleExecution.objects.values_list(
        "question_id", flat=True))
    approved_ids = set(QuestionApproval.objects.values_list(
        "question_id", flat=True))

    report = []
    for name in sorted(Topic.objects.values_list("name", flat=True)):
        questions = Question.objects.filter(topic__name=name)
        ids = set(questions.values_list("id", flat=True))
        trusted = questions.filter(Question.adaptive_eligible_q()).count()
        if trusted and not include_covered:
            continue

        servable = _servable_questions().filter(topic__name=name).count()
        coverage = TopicCoverage(
            name=name,
            depth=_depth(name, edges),
            questions=questions.count(),
            servable=servable,
            trusted=trusted,
            reference_solutions=len(ids & reference_ids),
            oracle_executions=len(ids & oracle_ids),
            approvals=len(ids & approved_ids),
            unlocks=sorted(forward.get(name, [])),
        )

        if trusted:
            coverage.blocker = ""
        elif servable == 0:
            # A distinct and worse problem: no reference can be authored for a
            # question that cannot be served in the first place.
            coverage.blocker = (
                "NO SERVABLE QUESTION — content repair required before any "
                "reference solution can be authored")
        else:
            coverage.blocker = BLOCKING_ARTIFACT
            coverage.candidates = rank_candidates(name, target_elo, limit)

        report.append(coverage)

    return report


def summarise(coverage):
    """Counts an operator can act on, and nothing derived beyond them."""
    blocked_on_content = [c for c in coverage
                          if c.trusted == 0 and c.servable == 0]
    blocked_on_reference = [c for c in coverage
                            if c.trusted == 0 and c.servable > 0]
    return {
        "topics_reported": len(coverage),
        "uncovered": len(blocked_on_content) + len(blocked_on_reference),
        "blocked_on_reference_authoring": len(blocked_on_reference),
        "blocked_on_content_repair": len(blocked_on_content),
        "content_repair_topics": sorted(c.name for c in blocked_on_content),
        "median_learner_elo": median_learner_elo(),
    }
