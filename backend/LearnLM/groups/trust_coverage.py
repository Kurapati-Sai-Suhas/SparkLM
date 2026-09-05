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

import ast
import builtins
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


#: Structural types a signature can declare that NOTHING deserializes.
#:
#: The v1/v2 harness reads one stdin line per parameter and parses it with
#: `_sparklm_parse`, which splits on whitespace and coerces tokens to
#: int/float/bool/str. There is no tree or linked-list builder anywhere in the
#: contract layer — `grep TreeNode groups/execution_contract.py` returns
#: nothing — so a signature declaring one receives a STRING like '[2,1,3]'.
#:
#: q98 Validate Binary Search Tree is the case that surfaced this: it was the
#: top-ranked candidate in this very report until the ranking learned to check.
STRUCTURAL_TYPES = frozenset({"TreeNode", "ListNode", "Node"})

#: What the harness makes available to user code at definition time: nothing
#: beyond builtins. It emits no imports, so an annotation naming `Optional`,
#: `List` or `TreeNode` raises NameError before the learner's first line runs.
_HARNESS_PROVIDES = frozenset(dir(builtins))


def _identifiers(text):
    """
    Bare identifiers inside a forward-reference annotation string.

    `'Optional[TreeNode]'` yields both names. Parsed rather than regexed so a
    quoted annotation is read the same way an unquoted one is; anything that
    does not parse yields nothing, because a report should not guess.
    """
    try:
        parsed = ast.parse(text, mode="eval")
    except SyntaxError:
        return set()
    return {node.id for node in ast.walk(parsed)
            if isinstance(node, ast.Name)}


def harness_blocker(python_boilerplate):
    """
    A PROVABLE reason this question cannot execute as declared, or None.

    STATIC analysis. Determining this by running the boilerplate would mean a
    read-only report executing repository content to decide what to print,
    which is a bad trade for a checkable property — so the annotations are
    read out of the AST and compared against what the harness defines.

    ── What None does NOT mean ─────────────────────────────────────────────

    None means "no blocker is provable from the signature". It does not mean
    the question works. Two failure classes are invisible here because the
    signature is silent about them:

      * an UNANNOTATED parameter — q199 Binary Tree Right Side View declares
        `rightSideView(self, root)` and is handed the string '[1,2,3,null,5]';
      * an annotation that is technically satisfiable but semantically wrong —
        q230 declares `root: list` for a level-order tree, so `_sparklm_parse`
        returns `['[3,1,4,null,2]']`, a one-element list holding a string.

    Catching those needs the stdin format compared against the parser, and the
    parser lives inside a wrapper TEMPLATE STRING rather than an importable
    function. Reimplementing it here would create a second definition of the
    calling convention, which is the drift this codebase repeatedly warns
    about. So this check proves what it can and the report says so plainly.
    """
    source = (python_boilerplate or "").strip()
    if not source:
        return "no python boilerplate"

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"boilerplate does not parse ({exc.msg})"

    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])

    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        annotations = [a.annotation for a in node.args.args if a.annotation]
        if node.returns:
            annotations.append(node.returns)
        for annotation in annotations:
            for child in ast.walk(annotation):
                if isinstance(child, ast.Name):
                    names.add(child.id)
                elif isinstance(child, ast.Constant) and isinstance(
                        child.value, str):
                    # A FORWARD REFERENCE: `root: 'TreeNode'`. Quoting is not
                    # a workaround — the name is still undefined, the string
                    # just defers the NameError from definition time to
                    # whenever something resolves it, and nothing here ever
                    # does. q742 Closest Leaf in a Binary Tree declares
                    # exactly this and slipped past the first version of this
                    # check, which only looked at `ast.Name`.
                    names.update(
                        part for part in _identifiers(child.value))

    structural = sorted(names & STRUCTURAL_TYPES)
    if structural:
        return (f"signature declares {', '.join(structural)}, which no "
                f"contract deserializes — the harness would pass a string")

    undefined = sorted(names - _HARNESS_PROVIDES - defined - imported)
    if undefined:
        return (f"annotation names {', '.join(undefined)}, which the harness "
                f"never defines — NameError before the learner's first line")

    return None


def rank_candidates(topic_name, target_elo, limit=3):
    """
    A mechanical shortlist for one topic. Suggestions, never decisions.

    Ordering:
      1. executable as declared                — a question whose boilerplate
         raises NameError, or whose signature names a type nothing builds,
         cannot be taken through the pipeline at all
      2. reachable at the median learner Elo   — an unreachable trusted
         question is verified content nobody is offered
      3. peers at that difficulty, descending  — a question competing with
         many others makes the preference visible
      4. question id                           — terminal, so the shortlist
         is reproducible

    Key 1 was added after this report recommended q98 Validate Binary Search
    Tree as the highest-leverage target and an operator would have spent a
    session discovering that its declared `Optional[TreeNode]` cannot be
    satisfied. Ranking on servability alone is not enough: 179 of the 1,788
    servable questions do not execute as declared.
    """
    from groups.coding_views import _servable_questions

    peers = dict(
        _servable_questions().filter(topic__name=topic_name)
        .values_list("base_difficulty")
        .annotate(n=Count("id")))

    rows = list(_servable_questions().filter(topic__name=topic_name)
                .values("id", "title", "base_difficulty", "boilerplate_code"))
    for row in rows:
        row["blocker"] = harness_blocker(
            (row["boilerplate_code"] or {}).get("python"))

    rows.sort(key=lambda r: (
        r["blocker"] is not None,
        not _reachable(r["base_difficulty"], target_elo),
        -peers.get(r["base_difficulty"], 0),
        r["id"],
    ))
    return [{
        "id": row["id"],
        "title": (row["title"] or "")[:44],
        "difficulty": float(row["base_difficulty"]),
        "reachable": _reachable(row["base_difficulty"], target_elo),
        "executable": row["blocker"] is None,
        "harness_blocker": row["blocker"],
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
