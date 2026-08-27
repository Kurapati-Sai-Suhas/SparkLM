"""
The tools an orchestrator may call (M2 P2.11a).

Every tool wraps a service that already exists. Nothing here computes
mastery, rating, routing policy or trust — it reads what the backend already
decided, because a second implementation of a rule is a second chance to get
it wrong.

── What the model may and may not do ───────────────────────────────────────

The model chooses WHICH tool to call and with WHAT ARGUMENTS. It never
touches the database.

    MAY     select a tool by name from the registry
    MAY     supply arguments, which are validated before use
    NEVER   write any row
    NEVER   update Glicko-2, mastery, or KT state
    NEVER   act for a user other than the authenticated one
    NEVER   name a question id the backend has not already offered

That last rule is the load-bearing one. `get_problem_context` and
`grade_submission` refuse an id that did not come from
`get_candidate_problems` in THIS session, so a hallucinated or guessed id
cannot reach a learner. The candidate set is held by the session, not by the
model, and the model cannot widen it.

── Glicko-2 is read-only here ──────────────────────────────────────────────

`groups/shadow.py` is UNARMED: `EloEngine` owns the rating a learner sees,
and Glicko-2 runs beside it on the same evidence. These tools READ the
shadow ability (`shadow.current_ability` persists nothing) and never write
it. Arming the shadow model is a decision with its own evidence and its own
phase; an orchestrator must not make it as a side effect.
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from django.utils import timezone

from groups import shadow
from groups.models import (CodeSubmission, Question, Topic, UserCodingProfile,
                           UserTopicMastery)


class ToolError(Exception):
    """A tool refused. The orchestrator turns this into a retry or a stop."""


class ToolDenied(ToolError):
    """The call was structurally valid but not permitted. Never retried."""


@dataclass
class Session:
    """
    What the ORCHESTRATOR knows and the model does not.

    The authenticated user and the set of question ids the backend has
    offered live here. The model can ask for a question by id; it cannot
    enlarge the set of ids that resolve.
    """

    user: Any
    offered_question_ids: set = field(default_factory=set)
    max_candidates: int = 20

    def offer(self, question_ids):
        ids = list(question_ids)[: self.max_candidates]
        self.offered_question_ids.update(ids)
        return ids

    def require_offered(self, question_id):
        if question_id not in self.offered_question_ids:
            raise ToolDenied(
                f"question {question_id} was not offered in this session. A "
                f"tool may only act on a question the backend has already "
                f"returned — this is what stops a guessed or hallucinated id "
                f"reaching a learner.")


# ═════════════════════════════════════════════════════════════
# The tools
# ═════════════════════════════════════════════════════════════

def get_learner_state(session):
    """The learner's current standing. Reads; computes nothing new."""
    user = session.user
    profile = UserCodingProfile.objects.filter(user=user).first()
    mastery = list(
        UserTopicMastery.objects.filter(user=user)
        .select_related("topic")
        # Field names read from the model, not assumed: UserTopicMastery
        # carries `reviews`, not `total_attempts`.
        .values("topic__name", "accuracy", "reviews", "last_practiced")[:25])

    solved = CodeSubmission.objects.filter(
        user=user, status="accepted").values("question_id").distinct().count()
    eligible = CodeSubmission.objects.filter(
        user=user, adaptive_eligible=True).count()

    return {
        "elo_rating": round(profile.elo_rating, 2) if profile else None,
        "rating_engine": "EloEngine (live)",
        "glicko_shadow": "available, UNARMED — not the learner-visible rating",
        "distinct_questions_solved": solved,
        "submissions_admissible_as_evidence": eligible,
        "topic_mastery": [
            {"topic": row["topic__name"],
             "accuracy": round(row["accuracy"], 3)
             if row["accuracy"] is not None else None,
             "reviews": row["reviews"],
             "last_practiced": row["last_practiced"]}
            for row in mastery],
    }


def get_candidate_problems(session, topic=None, limit=10):
    """
    Questions the backend is willing to serve, and the ONLY ids that become
    callable afterwards.

    Filtered on `is_adaptive_eligible` — PUBLISHED and ORACLE_VERIFIED — so
    an untrusted question cannot be recommended. That condition is read from
    the question, never recomputed here.
    """
    limit = max(1, min(int(limit or 10), session.max_candidates))
    queryset = (Question.objects
                .filter(status=Question.STATUS_PUBLISHED,
                        trust_state=Question.TRUST_ORACLE_VERIFIED)
                .select_related("topic"))
    if topic:
        queryset = queryset.filter(topic__name__iexact=str(topic))

    rows = list(queryset.order_by("base_difficulty", "id")[:limit])
    session.offer(row.pk for row in rows)
    return {
        "count": len(rows),
        "trust_filter": "PUBLISHED and ORACLE_VERIFIED only",
        "candidates": [
            {"question_id": row.pk, "title": row.title.strip(),
             "topic": row.topic.name if row.topic_id else None,
             "difficulty": row.base_difficulty}
            for row in rows],
    }


def get_prerequisites(session, topic):
    """
    The curriculum's own prerequisite view, read from the production DAG.

    Never rebuilt here. `hybrid_router` owns the graph and its cache; a
    second traversal would be a second answer.
    """
    from groups import hybrid_router

    name = str(topic or "").strip()
    if not name:
        raise ToolError("get_prerequisites needs a topic name")

    graphs = hybrid_router.get_curriculum_graphs()
    for subject, graph in (graphs or {}).items():
        if graph is None or name not in graph:
            continue
        return {
            "topic": name,
            "subject": subject,
            "prerequisites": sorted(graph.predecessors(name)),
            "unlocks": sorted(graph.successors(name)),
            "source": "production curriculum DAG (read-only)",
        }
    return {"topic": name, "subject": None, "prerequisites": [],
            "unlocks": [], "source": "topic not present in any curriculum DAG"}


def get_problem_context(session, question_id):
    """
    What a learner is allowed to see about one offered question.

    Deliberately NOT the hidden tests, the expected outputs or any
    reference. Those are the answer key; an orchestrator has no business
    reading them and a model that could read them could leak them.
    """
    question_id = _as_id(question_id)
    session.require_offered(question_id)
    question = Question.objects.select_related("topic").filter(
        pk=question_id).first()
    if question is None:
        raise ToolError(f"no such question: {question_id}")

    return {
        "question_id": question.pk,
        "title": question.title.strip(),
        "topic": question.topic.name if question.topic_id else None,
        "difficulty": question.base_difficulty,
        "statement_html": question.content,
        "starter_code": question.boilerplate_code or {},
        "withheld": ["hidden_test_cases", "expected outputs",
                     "reference solution"],
    }


def get_tutor_context(session, question_id):
    """
    Grounding for an explanation, for a question the learner has attempted.

    Returns the learner's OWN attempt history — never another learner's, and
    never the answer key.
    """
    question_id = _as_id(question_id)
    session.require_offered(question_id)

    attempts = list(
        CodeSubmission.objects
        .filter(user=session.user, question_id=question_id)
        .order_by("-id")
        .values("status", "language", "execution_time_ms")[:5])

    question = Question.objects.filter(pk=question_id).first()
    if question is None:
        raise ToolError(f"no such question: {question_id}")

    return {
        "question_id": question_id,
        "title": question.title.strip(),
        "statement_html": question.content,
        "learner_attempts": attempts,
        "attempt_count": len(attempts),
        "withheld": ["hidden_test_cases", "expected outputs",
                     "reference solution", "other learners' submissions"],
    }


def grade_submission(session, question_id, language, source, commit=False,
                     runner=None):
    """
    Grade a learner's code. THE ONLY TOOL THAT CAN TOUCH THE DATABASE, and
    only when the ORCHESTRATOR passes commit=True — never the model.

    `commit` is not a tool argument the model can supply: `Orchestrator`
    strips it from every model-supplied payload. A model that asks to commit
    is asking for something the transport cannot express.
    """
    question_id = _as_id(question_id)
    session.require_offered(question_id)
    question = Question.objects.filter(pk=question_id).first()
    if question is None:
        raise ToolError(f"no such question: {question_id}")
    if not isinstance(source, str) or not source.strip():
        raise ToolError("grade_submission needs source code")

    from groups.services import GradingService

    # The runner is INJECTED, exactly as the view does it. Constructing a
    # default here would give the agent its own execution path, and two
    # execution paths are two sets of semantics — the defect Phase 19 found
    # in a gate that wrapped mutants differently from the grader.
    if runner is None:
        from groups.coding_views import _run_on_judge0 as runner

    grade = GradingService(runner=runner).grade(question, language, source)
    result = {
        "question_id": question_id,
        "all_passed": grade.all_passed,
        "status": grade.final_status,
        "persisted": False,
    }
    if not commit:
        return result

    from groups.services import ProgressionService

    submission, elo_result, _profile = ProgressionService.apply_submission(
        session.user, question, language, question.base_difficulty, grade)
    result.update({"persisted": True, "submission_id": submission.pk,
                   "rating_change": elo_result.get("rating_change")})
    return result


def _as_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError(f"question id must be an integer, got {value!r}")


# ═════════════════════════════════════════════════════════════
# The registry
# ═════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Tool:
    name: str
    handler: Callable
    reads_only: bool
    description: str
    required: tuple = ()
    optional: tuple = ()

    def validate(self, arguments):
        """Structured arguments, checked before the handler sees them."""
        if not isinstance(arguments, dict):
            raise ToolError(f"{self.name}: arguments must be an object")
        allowed = set(self.required) | set(self.optional)
        unknown = set(arguments) - allowed
        if unknown:
            raise ToolError(
                f"{self.name}: unknown argument(s) {sorted(unknown)}; "
                f"accepted: {sorted(allowed) or 'none'}")
        missing = [name for name in self.required if name not in arguments]
        if missing:
            raise ToolError(f"{self.name}: missing {missing}")
        return {k: arguments[k] for k in arguments}


REGISTRY = {t.name: t for t in (
    Tool("get_learner_state", get_learner_state, True,
         "The learner's rating, solved count and topic mastery."),
    Tool("get_candidate_problems", get_candidate_problems, True,
         "Trusted questions the backend will serve. Only ids returned here "
         "become usable by later tools.",
         optional=("topic", "limit")),
    Tool("get_prerequisites", get_prerequisites, True,
         "Prerequisites and unlocks for a topic, from the production DAG.",
         required=("topic",)),
    Tool("get_problem_context", get_problem_context, True,
         "Learner-visible detail for one offered question.",
         required=("question_id",)),
    Tool("get_tutor_context", get_tutor_context, True,
         "Grounding for an explanation, including the learner's own attempts.",
         required=("question_id",)),
    Tool("grade_submission", grade_submission, False,
         "Grade code against a question. Persists only when the orchestrator "
         "commits.",
         required=("question_id", "language", "source")),
)}

#: What the UI is told, per tool. High-level and human — never the model's
#: reasoning. The orchestrator emits these; nothing else is surfaced.
NARRATION = {
    "get_learner_state": "Checking learner state",
    "get_candidate_problems": "Looking for suitable problems",
    "get_prerequisites": "Looking at prerequisites",
    "get_problem_context": "Reading the problem",
    "get_tutor_context": "Gathering context for an explanation",
    "grade_submission": "Running the code",
}
