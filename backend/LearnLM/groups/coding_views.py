import os
import base64
import requests
import logging
import time
from datetime import timedelta

from django.core.cache import cache
from django.db.models import (
    Case, Count, Exists, F, Func, IntegerField, Max, OuterRef, Subquery, Value,
    When,
)
from django.db.models.functions import Coalesce, Floor
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from common import languages
from common.throttling import ClientIPScopedRateThrottle
from groups import execution_contract, language_readiness

# Import Models
from .models import (
    CodingPortal, Topic, Question, UserCodingProfile, CodeSubmission, UserBadge, Badge,
    UserTopicMastery, AgenticCoachLog, RecommendationLog
)
from .serializers import CodeSubmitSerializer, CodingPortalSerializer
from .models import CodingPortal

# Import AI Engines & Services
from .hybrid_router import (
    ROUTING_POLICY_VERSION,
    HierarchicalEngine,
    compute_routing_telemetry, get_mastered_topic_names,
    log_routing_decision,
)
from .services import (
    ExecutionContractError, GradingService, GradingUnavailable,
    ProgressionService,
)
# `generate_test_cases` is deliberately NOT imported here (M2 P2.5). Grading
# data must never be produced inside a learner request; see the note in
# AdaptiveProblemView where the fallback used to live.
from .engines.tensor_builder import TensorBuilder


logger = logging.getLogger(__name__)


# Derived from common.languages (M4 Phase B) rather than hand-maintained.
# This map and the serializer's allowed set drifted twice: 'js' was here
# without 'javascript' (breaking every JS submission), and 'c' was in here
# but missing from the serializer (breaking every C submission). One
# registry, so they cannot disagree again. Name kept for existing call
# sites and tests.
LANGUAGE_IDS = languages.LANGUAGE_IDS

# Host and base URL are config-driven; the host header MUST match the
# RapidAPI product the key is registered for (settings.py previously
# defaulted to judge0-extra-ce while this file hardcoded judge0-ce).
JUDGE0_HOST = os.environ.get('JUDGE0_API_HOST', 'judge0-ce.p.rapidapi.com')
JUDGE0_BASE = os.environ.get('JUDGE0_URL', f'https://{JUDGE0_HOST}')
JUDGE0_KEY  = os.environ.get('JUDGE0_API_KEY')

# Learner-facing verdict text (M2 P2.5). Deliberately says nothing about WHICH
# hidden case failed or what it expected — naming the case is itself a leak,
# because repeated submissions would let a learner bisect the suite.
SUBMIT_VERDICT_MESSAGES = {
    "accepted":      "Accepted: your solution passed every hidden test.",
    "wrong_answer":  "Wrong Answer: your solution failed one or more hidden tests.",
    "time_limit":    "Time Limit Exceeded: your solution was too slow on one or more hidden tests.",
    "compile_error": "Compilation Error: your code did not compile.",
    "runtime_error": "Runtime Error: your code crashed on one or more hidden tests.",
}


def _servable_questions():
    """
    Base queryset of questions eligible to be served by the recommender.

    Two quarantines, same idea: content that cannot deliver the full solve
    loop never reaches a user. Placeholder rows lack real descriptions;
    beyond those, ~1,100 CSV-imported rows carry a genuine description but
    ZERO judge test cases (they look seeded, so reseed skips them) — serving
    one yields an empty sample case and a guaranteed submit failure. Both
    stay invisible until the content pipeline arms them.
    """
    return Question.objects.exclude(
        content__icontains=Question.PLACEHOLDER_MARKER
    ).exclude(hidden_test_cases=[]).exclude(hidden_test_cases__isnull=True)


def _servable_question(problem_id):
    """
    The question behind `problem_id` IF it is servable, else None (M2 P2.7h-13).

    The recommendation path has always filtered on `_servable_questions()`,
    but the direct-id endpoints re-fetched with a bare
    `Question.objects.get(id=...)` — so knowing an integer was enough to reach
    a question the selector deliberately excludes. Run built and executed its
    wrapper; Submit graded against its hidden tests and wrote a
    `CodeSubmission`. The quarantine was real for the recommendation path and
    decorative everywhere else.

    Derived from `_servable_questions()` rather than restating its exclusions,
    so the predicate has ONE definition. Restating it is how the two paths
    disagreed in the first place, and a future change to the serving rule
    (P2.7h-12 left `status == PUBLISHED` open) must not need finding again.
    """
    try:
        return _servable_questions().filter(pk=problem_id).first()
    except (ValueError, TypeError):
        # A malformed id is "not a servable question", not a 500. `None` needs
        # no special case: Django renders it as `pk IS NULL`, which matches
        # nothing. An explicit blank-id guard here as well would be a second
        # mechanism for the same outcome — and a mutation sweep proved it
        # unkillable, which is how a redundant check hides a missing one.
        return None


#: How long a failed question stays demoted (M2 P2.8a).
#:
#: PROVISIONAL AND NOT DERIVED FROM DATA. There is no telemetry on session
#: length, attempts per problem, or how often a learner returns, so 24 hours is
#: a judgement call, not a measurement. It is a module constant rather than a
#: setting on purpose: a knob invites tuning by feel, and nobody currently has
#: the evidence to tune it. Revisit when P2.7e and real usage telemetry exist.
FAILED_QUESTION_COOLDOWN = timedelta(hours=24)

#: Width of one difficulty tier, for trust-aware exposure (M2 P2.31).
#:
#: NOT a new tuning knob. It is the spacing of the bank's own difficulty
#: ladder — `reseed_generation.DIFFICULTY_BANDS` is
#: {1000: easy, 1300: medium, 1600: hard}, and 983/409/395 of the servable
#: questions sit exactly on those three rungs. Two questions within 300 Elo of
#: each other are on the same or adjacent rung and are, for exposure purposes,
#: comparably hard.
#:
#: Why a band at all, rather than preferring trust on an exact-difficulty tie:
#: it would have accomplished almost nothing. Five of the six verified
#: questions sit at 1000 and 17 of 20 learners sit at the 1200 default, so
#: their |difficulty - elo| is 200 while 983 unverified questions at 1300
#: score 100. Trust would have lost key 1 every time and never been consulted.
EXPOSURE_ELO_BAND = 300.0


def _difficulty_band(elo_diff_expression):
    """
    `elo_diff` bucketed into tiers of `EXPOSURE_ELO_BAND`.

    Integer division, so the result is monotonic non-decreasing in elo_diff.
    That monotonicity is the whole safety argument for the ordering below:
    `test_untrusted_ordering_is_unchanged_by_the_new_policy` pins it, and
    `test_the_exact_difficulty_key_still_separates_questions_in_one_band`
    catches the mutant where the band is left to stand alone.
    """
    return Floor(elo_diff_expression / Value(EXPOSURE_ELO_BAND))


def _candidate_questions(user, topic_name=None, now=None):
    """
    Servable questions for `user`, ordered by the P2.8a policy (M2 P2.8a).

    ONE set-based query. The previous implementation pulled every accepted
    `question_id` into Python and passed it back as `NOT IN (...)`, an IN-list
    that grew without bound for the learner it was meant to serve, and then
    ordered by `ABS(base_difficulty - elo)` alone — with `base_difficulty`
    taking one of three values across the whole bank, that left ties hundreds
    of rows deep, resolved by whatever order PostgreSQL happened to return.
    Same learner, same state, arbitrary and irreproducible answer.

    The exposure terms come from `CodeSubmission` only, served by the existing
    `subm_user_q_ts_idx (user, question, -submitted_at)`. `RecommendationLog`
    is deliberately not consulted: an impression is not an interaction, and a
    learner who was shown a problem and navigated away must still be able to
    receive it.

    ── Ordering ────────────────────────────────────────────────────────────

        1. |base_difficulty - target_elo|   existing intent, preserved
        2. failed within the cooldown       not-in-cooldown first
        3. attempt count                    least-seen first
        4. last attempt                     ascending, NULLs (never tried) first
        5. question id                      terminal, makes the order TOTAL

    Key 5 is not cosmetic. Without a unique final key the ordering is only
    partial, and every guarantee above it is void.

    ── Demote, never exclude ───────────────────────────────────────────────

    Cooldown is an ORDERING TERM. A filter could empty the candidate set and
    force the fallback — the very path that used to cross topics silently — so
    the invariant "cooldown never empties the set" is satisfied structurally
    rather than by a guard somebody has to remember. In the worst case every
    candidate is cooling down and the least-recently-failed one is returned,
    which is the right answer.

    ── Trust boundary ──────────────────────────────────────────────────────

    This reads `status` from submissions that may be UNVERIFIED. That is
    permitted and safe: it decides WHAT WE SHOW, never WHAT WE BELIEVE (P2.7c).
    No rating, mastery, telemetry or tensor value is touched here. And the
    direction is right — if a question's answer key is wrong the learner fails
    it, and moving them off it is exactly the correct response.
    """
    now = now or timezone.now()
    attempts = CodeSubmission.objects.filter(user=user, question=OuterRef("pk"))

    queryset = _servable_questions()
    if topic_name is not None:
        queryset = queryset.filter(topic__name=topic_name)

    return (
        queryset
        .annotate(
            solved=Exists(attempts.filter(status="accepted")),
            attempt_count=Coalesce(
                Subquery(
                    attempts.values("question")
                    .annotate(n=Count("pk"))
                    .values("n")[:1],
                    output_field=IntegerField(),
                ),
                Value(0),
            ),
            last_attempt_at=Subquery(
                attempts.values("question")
                .annotate(latest=Max("submitted_at"))
                .values("latest")[:1]
            ),
        )
        # A question the learner has already solved is gone from the practice
        # route for good. This is the one hard exclusion.
        .filter(solved=False)
        .annotate(
            in_cooldown=Case(
                When(last_attempt_at__gte=now - FAILED_QUESTION_COOLDOWN,
                     then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
    )


def _select_question(user, topic_name, target_elo, now=None):
    """
    The single next question for `user` in `topic_name`, or None (M2 P2.8a).

    The whole ordering in one place so both routing branches and every test go
    through the same code rather than two hand-synchronised copies.

    ── Trust-aware exposure (M2 P2.31) ─────────────────────────────────────

    The recommender was trust-blind: `_servable_questions()` filters on
    deliverability, and none of the five ordering keys mentioned trust. Six
    verified questions against 1,788 servable ones is 0.336% of the pool, so
    across the 218 recommendations ever logged the expected number landing on
    verified content was 0.73. Observed: zero. No adaptive-eligible submission
    has ever existed, so Traffic Cop has never received a trustworthy outcome
    and cannot be evaluated. That is the loop this key exists to start.

    Trust enters as an ORDERING TERM, never a filter — the same shape as
    `in_cooldown`, and for the same reason. A trust FILTER would cut the
    candidate set from 1,788 to 6 and collapse the product; an ordering term
    cannot empty it, so the fallback to unverified content is structural
    rather than a guard someone has to remember.

    ── Why it cannot distort difficulty ────────────────────────────────────

    Key 1 is the difficulty BAND and key 3 is the exact difference, so for any
    two questions of EQUAL trust the pair (band, diff) orders exactly as diff
    alone did — `floor(d / 300)` is monotonic in d. The unverified-only
    ordering is therefore unchanged, provably and by test. The single
    behavioural difference is that a verified question overtakes an unverified
    one when both are already in the same difficulty band.

    Trust is read from `is_adaptive_eligible`, the existing definition. This
    function does not re-derive, widen, or cache it, and writes nothing.
    """
    return (
        _candidate_questions(user, topic_name=topic_name, now=now)
        .annotate(
            elo_diff=Func(F("base_difficulty") - target_elo, function="ABS"),
        )
        .annotate(
            elo_band=_difficulty_band(F("elo_diff")),
            # 0 sorts before 1, so ascending order puts trusted first. Named
            # for the value that sorts LAST to keep the direction obvious at
            # the call site.
            untrusted=Case(
                When(Question.adaptive_eligible_q(), then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
        .order_by(
            "elo_band",                              # 1. difficulty tier
            "untrusted",                             # 2. verified first (P2.31)
            "elo_diff",                              # 3. nearest difficulty
            "in_cooldown",                           # 4. not recently failed
            "attempt_count",                         # 5. least seen
            F("last_attempt_at").asc(nulls_first=True),  # 6. longest ago / never
            "id",                                    # 7. TOTAL order
        )
        .first()
    )


#: How many alternatives to offer when a topic runs dry. Small on purpose —
#: this is a signpost, not a recommendation.
SUGGESTED_TOPIC_LIMIT = 5


def _topics_with_candidates(user, limit=SUGGESTED_TOPIC_LIMIT, now=None):
    """
    Topic names that still hold at least one question this learner can attempt.

    Deliberately NOT a ranking. It is the same candidate filter with the topic
    constraint dropped, projected to topic names and sorted alphabetically so
    the list is reproducible. Inventing a cross-topic recommender is P2.9 work;
    this only answers "where else is there anything left?".
    """
    return sorted(
        _candidate_questions(user, now=now)
        .values_list("topic__name", flat=True)
        .distinct()
    )[:limit]


def _run_on_judge0(source_code: str, language: str, stdin: str = "") -> dict:
    language_id = LANGUAGE_IDS.get(language.lower())
    if not language_id:
        return {"error": f"Unsupported language '{language}'. Use: {list(LANGUAGE_IDS.keys())}"}

    payload = {
        "source_code":    base64.b64encode(source_code.encode()).decode(),
        "language_id":    language_id,
        "stdin":          base64.b64encode(stdin.encode()).decode() if stdin else "",
        "base64_encoded": True,
        "wait":           True,
    }
    # Opt-in only, empty unless an operator configures it (M2 P2.6). Judge0
    # rejects submissions exceeding its server-side max_cpu_time_limit, and
    # ours is UNKNOWN from this repository — JUDGE0_API_HOST is `sync: false`.
    # A rejected submission becomes GradingUnavailable, so guessing here risks
    # a 503 on every submission.
    payload.update(execution_contract.judge0_resource_limits())
    
    headers = {
        "Content-Type":    "application/json",
        "X-RapidAPI-Key":  JUDGE0_KEY,
        "X-RapidAPI-Host": JUDGE0_HOST,
    }

    def decode(val):
        if not val:
            return ""
        try:
            return base64.b64decode(val).decode('utf-8', errors='replace')
        except Exception:
            return val

    try:
        res = requests.post(
            f"{JUDGE0_BASE}/submissions?base64_encoded=true&wait=true",
            json=payload, headers=headers, timeout=15
        )
        res.raise_for_status()
        data = res.json()
        return {
            "status":         data.get("status", {}).get("description", "Unknown"),
            "status_id":      data.get("status", {}).get("id"),
            "stdout":         decode(data.get("stdout")),
            "stderr":         decode(data.get("stderr")),
            "compile_output": decode(data.get("compile_output")),
            "time":           data.get("time"),
            "memory":         data.get("memory"),
        }
    except requests.Timeout:
        return {"error": "Judge0 timed out. Try again."}
    except requests.RequestException as e:
        return {"error": f"Judge0 request failed: {str(e)}"}


class GamificationDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 1. Get current streak
        profile, _ = UserCodingProfile.objects.get_or_create(user=request.user)
        streak = profile.current_streak

        # 2. Get global leaderboard (Top 3 by Elo) — identical for every
        # user viewing the dashboard at any given moment, so it's cached
        # for a short window instead of re-running the ordered query (plus
        # a user lookup per row) on every single dashboard load.
        leaderboard = cache.get("gamification_leaderboard_top3")
        if leaderboard is None:
            top_profiles = UserCodingProfile.objects.select_related('user').order_by('-elo_rating')[:3]
            leaderboard = [
                {
                    "rank": idx + 1,
                    "name": p.user.get_full_name() or p.user.username,
                    "handle": f"@{p.user.username}",
                    "elo": int(p.elo_rating),
                }
                for idx, p in enumerate(top_profiles)
            ]
            cache.set("gamification_leaderboard_top3", leaderboard, timeout=60)

        # 3. Get recent badges
        recent_badges = UserBadge.objects.filter(user=request.user).select_related('badge').order_by('-awarded_at')[:3]
        badges = []
        for ub in recent_badges:
            badges.append({
                "id": ub.badge.badge_id,
                "name": ub.badge.name,
                "description": ub.badge.description,
                "color": ub.badge.color,
                "icon": ub.badge.icon_name
            })
            
        return Response({
            "streak": streak,
            "leaderboard": leaderboard,
            "badges": badges
        })


class CodeRunView(APIView):
    """
    REQ-3.2: Run code in isolation (Used for the "Run Code" button).
    POST /api/code/run/
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = 'judge0'

    def post(self, request):
        raw_code = request.data.get('code', '').strip()
        language = request.data.get('language', 'python')
        stdin    = request.data.get('stdin', '')
        problem_id = request.data.get('problem_id', None)
        
        if not raw_code:
            return Response({"error": "code is required"}, status=400)

        # Build exactly what Submit would build (M1/P1.2-A).
        #
        # This used to apply ONLY a per-question hidden_wrapper_code entry and
        # fall through to the raw source otherwise. Question.hidden_wrapper_code
        # defaults to {}, so for most questions Run executed a bare
        # `class Solution: ...` — which defines a class, calls nothing, and
        # prints nothing. The learner saw a green status with empty output while
        # Submit, which falls back to the generic harness, graded the same code
        # correctly. Java diverged further: Submit strips imports before
        # wrapping, Run did not, so identical source could compile under Submit
        # and fail under Run.
        #
        # Delegating to GradingService keeps ONE implementation of "what does
        # this source actually execute as". Divergence between Run and Submit is
        # a correctness bug by construction, not something to keep in sync by
        # hand — which is how the two drifted in the first place.
        executable_code = raw_code
        if problem_id:
            # Unknown, malformed, OR not servable: run the source as written
            # rather than 404 (M2 P2.7h-13). Run is a scratchpad, and refusing
            # to execute would be a behaviour regression for callers that omit
            # a valid id — so an unservable question degrades to exactly the
            # unknown-id path that already existed, with no new status code
            # and no client change.
            #
            # It matters because the question is what supplies the wrapper and
            # starter used to build the executable: without this, a question
            # the selector excludes could still have its harness executed by
            # anyone who knew its id.
            question = _servable_question(problem_id)
            if question is not None:
                executable_code, _ = GradingService._build_executable(
                    question, language, raw_code
                )

        result = _run_on_judge0(executable_code, language, stdin)
        
        if "error" in result:
            return Response(result, status=400)
        return Response(result)


class CodeSubmitView(APIView):
    """
    REQ-3.2 + REQ-3.3: Submit code against hidden test cases, log result, update Elo.
    POST /api/code/submit/

    Thin orchestrator over the service layer (frozen architecture §2.4.6):
    grading, persistence, coaching, and learning updates live in
    groups/services.py. The call order below is the behavioral contract —
    see the services module docstring before reordering anything.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = 'judge0'

    def post(self, request):
        serializer = CodeSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clean = serializer.validated_data

        raw_code   = clean['code']
        language   = clean['language']
        problem_id = clean['problem_id']

        if not Question.objects.filter(pk=problem_id).exists():
            return Response({"error": "Question not found"}, status=404)

        # Servable, or refused — the SAME predicate the recommendation path
        # uses (M2 P2.7h-13). Previously this re-fetched the row directly and
        # checked only for empty hidden tests, so a placeholder question the
        # selector excludes could still be graded by id.
        question = _servable_question(problem_id)
        if question is None:
            # 409, not 500 (M4 Phase B). An unservable question is a
            # data-integrity condition, not a server fault: the code is
            # behaving correctly and the client can do nothing about it
            # either way. Reporting it as 500 polluted error metrics with a
            # content problem and made real faults harder to see.
            #
            # Reaching this means the client asked for a specific question by
            # id that `_servable_questions()` excludes — no test cases, or a
            # placeholder statement. The status and `detail` are unchanged
            # from when this covered only the empty-suite case, so clients
            # that already handle `question_not_gradable` need no change.
            return Response(
                {"error": "Question misconfigured: not available for grading",
                 "detail": "question_not_gradable"},
                status=409,
            )

        # NO language gate here, deliberately (M2 P2.35).
        #
        # The first version of this milestone refused a submission whose
        # language failed `language_readiness`. That judged the learner by the
        # STARTER: readiness describes the code the platform HANDS OUT, and
        # what actually runs is the code the learner wrote. A C++ learner who
        # supplies a complete program with `main` is entitled to be graded
        # even though the shipped starter has none, and 28 existing tests —
        # questions that carry no boilerplate at all — said so immediately.
        #
        # Readiness belongs where the language is CHOSEN, not where code is
        # submitted, so it is reported by `NextProblemView` instead.

        # The runner is resolved from module globals at request time, so
        # the test seam (monkeypatching coding_views._run_on_judge0) keeps
        # working across the service boundary.
        try:
            grade = GradingService(runner=_run_on_judge0).grade(question, language, raw_code)
        except GradingUnavailable as exc:
            return Response(
                {"error": "Code Execution Service Unavailable", "details": exc.details},
                status=503,
            )
        except ExecutionContractError as exc:
            # 409, for the same reason as the no-test-cases branch above: the
            # question's stored test data cannot be executed as written, which
            # is a data-integrity condition rather than a server fault. 48
            # production questions store a list where text is required.
            #
            # ONLY this exception. A Judge0 outage stays 503 so the client
            # keeps retrying something that will work later, and any other
            # exception stays a 500 — laundering a genuine programming fault
            # into a content-problem label would hide exactly the bugs this
            # status is meant to stop hiding.
            #
            # `exc.details` names the failing case and is logged, never
            # returned: it describes the hidden test data, and grading data
            # never leaves the server (M2 P2.5).
            logger.error(
                "Execution contract failure on question %s (%s): %s",
                question.pk, language, exc.details,
            )
            return Response(
                {"error": "Question misconfigured: test data cannot be executed",
                 "detail": "question_not_gradable"},
                status=409,
            )

        submission, elo_result, profile = ProgressionService.apply_submission(
            user=request.user,
            question=question,
            language=language,
            difficulty=question.base_difficulty,
            grade=grade,
        )
        # Coach webhook strictly after commit — network calls are forbidden
        # inside the learner-state transaction (§2.2).
        agentic_hint = ProgressionService.coach_hint(
            user=request.user, problem_id=problem_id, question=question, grade=grade,
        )

        # `test_results` is deliberately ABSENT (M2 P2.5). It carried, per
        # hidden case, both `expected_output` — the answer key — and
        # `your_output`, which is worse than it looks: a submission of
        # `print(input())` echoes the hidden INPUT back through it, so the
        # pair let any learner reconstruct the entire hidden suite from
        # ordinary API responses. `_sample_case` 200 lines up already states
        # the rule this violated: "Grading data never leaves the server."
        #
        # The full per-case detail still exists server-side — ProgressionService
        # stores it on AgenticCoachLog.error_logs and reads timings from it —
        # it simply stops being serialised to the client.
        #
        # No production frontend code read `test_results`; the portal renders
        # all_passed / status / passed / total / agentic_hint.
        return Response({
            "submission_id": submission.id,
            "status":        grade.final_status,
            "message":       SUBMIT_VERDICT_MESSAGES.get(
                                 grade.final_status, "Submission graded."),
            "passed":        grade.passed,
            "total":         grade.total,
            "all_passed":    grade.all_passed,
            "runtime_ms":    submission.execution_time_ms,
            "memory_kb":     submission.memory_used_kb,
            "elo_update":    elo_result,
            "success_rate":  profile.success_rate,
            "agentic_hint":  agentic_hint,
        })


class CodingProfileView(APIView):
    """
    GET /api/code/profile/ — Returns Elo, stats, and last 10 submissions.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile, _ = UserCodingProfile.objects.get_or_create(user=request.user)
        recent = CodeSubmission.objects.filter(user=request.user).order_by('-submitted_at')[:10]

        return Response({
            "elo_rating":             profile.elo_rating,
            "total_submissions":      profile.total_submissions,
            "successful_submissions": profile.successful_submissions,
            "success_rate":           profile.success_rate,
            "recent_submissions": [
                {
                    "problem_id":        s.question_id,
                    "language":          s.language,
                    "status":            s.status,
                    "execution_time_ms": s.execution_time_ms,
                    "memory_used_kb":    s.memory_used_kb,
                    "submitted_at":      s.submitted_at.strftime("%d %b %Y %H:%M"),
                }
                for s in recent
            ],
        })


class NextProblemView(APIView):
    """
    REQ-4.2 & REQ-4.3 & REQ-4.4: The Traffic Cop (Hybrid Routing).
    Routes to GNN for structured topics (DSA), or Elo for unstructured topics (Trivia).
    GET /api/code/next/?topic=Array
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = 'recommend'

    def get(self, request):
        from .hybrid_router import RoutingClassifier
        profile, _ = UserCodingProfile.objects.get_or_create(user=request.user)
        target_elo = profile.elo_rating
        
        # Default to 'Array' if no topic is provided by the frontend
        topic_name = request.query_params.get('topic', 'Array')
        
        # An unknown topic is a client error, not an invitation to guess
        # (M2 P2.8b — B11).
        #
        # This used to fall back to `Topic.objects.first()` — an arbitrary row
        # in unspecified order — so a typo in the query string silently served
        # a question from some unrelated topic while the portal's badge still
        # showed what had been asked for. A 400 naming the topic is the only
        # answer that lets the caller fix it.
        try:
            topic = Topic.objects.get(name__iexact=topic_name)
        except Topic.DoesNotExist:
            return Response({
                "error": "unknown_topic",
                "message": f"No topic named '{topic_name}'.",
                "requested_topic": topic_name,
            }, status=400)

        # Solved exclusion and exposure ordering are now one set-based query
        # (M2 P2.8a) — see `_candidate_questions`. The unbounded Python
        # NOT IN (...) list this replaced grew with the learner's own history.

        question = None
        xai_explanation = ""
        advanced_data = None 

        # 🚀 ADVANCED ML: Sequence-Based LSTM (Deep Knowledge Tracing)
        # (Disabled for production - experimental code removed to reduce cold starts and memory usage)

        # M7 server-side curriculum gate (staged: enforced only when
        # CURRICULUM_GATE_ENFORCE is on — flipping request behavior right
        # before the demo window is deliberately avoided; the frontend
        # already guards Start on locked topics).
        from django.conf import settings as dj_settings
        if topic and getattr(dj_settings, 'CURRICULUM_GATE_ENFORCE', False):
            from .models import TopicPrerequisite
            prereqs = set(TopicPrerequisite.objects.filter(topic=topic)
                          .values_list('prerequisite__name', flat=True))
            missing = prereqs - set(get_mastered_topic_names(request.user))
            if missing:
                return Response({
                    "error": "topic_locked",
                    "message": f"Complete the prerequisites first: {', '.join(sorted(missing))}.",
                    "missing_prerequisites": sorted(missing),
                }, status=403)

        # 🚥 ML-BASED TRAFFIC COP
        router = RoutingClassifier()
        # FR-RTR-01 v2: mean + runs-test streakiness over the last 20
        # submissions (frozen architecture §6.2).
        avg_acc, runs_z, sample_size = compute_routing_telemetry(request.user)
        # Timed so the structured line can carry routing latency. The clock
        # brackets the decision ONLY — telemetry is already computed above and
        # is not re-read for the log.
        _route_started = time.monotonic()
        route_decision = router.predict_route(avg_acc, runs_z, target_elo / 2000.0)
        log_routing_decision(
            request.user, route_decision, avg_acc, runs_z, sample_size,
            router=router, elo=target_elo,
            latency_ms=(time.monotonic() - _route_started) * 1000.0,
        )

        # ROUTE 1: HIERARCHICAL (DAG PREREQUISITE TRAVERSAL)
        if route_decision == 'hierarchical' and topic:
            logger.info("[Traffic Cop] Routing to Hierarchical DAG engine for %s", topic.name)

            # SRS FR-HRCH-01: mastery = accuracy >= 0.8, shared definition.
            # (The old per-topic elo_rating >= 1300 check could never be
            # satisfied — per-topic Elo is not updated anywhere — so the
            # DAG recommended the root topic forever.)
            mastered_topics = get_mastered_topic_names(request.user)

            portal_name = topic.portal.name if topic.portal else "dsa"
            optimal_node = HierarchicalEngine.get_next_topic(portal_name, mastered_topics)

            target_topic_name = optimal_node.get("recommended_topic") or topic.name
            xai_explanation = optimal_node.get("reason", "")

            question = _select_question(request.user, target_topic_name, target_elo)

        # 🚥 ROUTE 2: FLAT (ELO ENGINE)
        else:
            logger.info("[Traffic Cop] Routing to Flat Elo engine for %s", topic.name if topic else "fallback")
            xai_explanation = f"📈 Matched to your current skill level (Elo: {round(target_elo)})."

            target_topic_name = topic.name if topic else None
            if topic:
                question = _select_question(request.user, topic.name, target_elo)

        if not question:
            # The global `.first()` fallback that used to live here is GONE
            # (M2 P2.8a). It ignored topic AND difficulty, so a learner who
            # exhausted Dynamic Programming silently received an arbitrary Bit
            # Manipulation question while the portal's topic badge still read
            # "Dynamic Programming". Crossing topics is a product decision, not
            # something a `.first()` should make by accident.
            #
            # Two distinct conditions, reported distinctly, because they need
            # completely different responses from the learner:
            if _candidate_questions(request.user).exists():
                return Response({
                    'status': 'topic_exhausted',
                    'message': (
                        f"You've worked through everything available in "
                        f"{target_topic_name or topic_name}. Pick another topic "
                        f"to keep going."
                    ),
                    'requested_topic': topic_name,
                    'served_topic': None,
                    'topic_substituted': False,
                    'suggested_topics': _topics_with_candidates(request.user),
                    'next_problem': None,
                }, status=200)

            return Response({
                'status': 'completed',
                'message': 'You have solved all available problems! New problems coming soon.',
                'mastery_percentage': 100.0,
                'next_problem': None,
            }, status=200)

        # 🧠 XAI PAYLOAD — computed once, after the final question is known
        from .engines.hlr_engine import HLREngine
        mastery = UserTopicMastery.objects.filter(user=request.user, topic=question.topic).first()
        if mastery:
            days_since = (timezone.now() - mastery.last_practiced).total_seconds() / 86400.0
            halflife = mastery.hlr_halflife
        else:
            days_since = 0.0
            halflife = 1.0
        hlr_state = HLREngine.calculate_memory_state(days_since, halflife)
        xai_payload = self._compute_xai(request.user, question.topic.name, hlr_state)
        advanced_data = {
            "xai": xai_payload,
            "decay_info": {"decay_percent": round((1.0 - hlr_state) * 100, 1)},
        }

        # 🚀 LOG THE RECOMMENDATION TO THE DATA FLYWHEEL
        prob = advanced_data.get("xai", {}).get("success_probability", None) if advanced_data else None
        RecommendationLog.objects.create(
            user=request.user,
            recommended_topic=question.topic,
            engine_used=route_decision,
            predicted_success_prob=prob,
            problem_id=str(question.pk),
            # M4 Phase B: which policy chose this. Bump
            # ROUTING_POLICY_VERSION in hybrid_router.py when routing
            # behaviour changes — this cannot be backfilled.
            policy_version=ROUTING_POLICY_VERSION,
            # M2 P2.31: was the question we actually served trusted? Read from
            # the same property the submission path freezes, at the moment of
            # exposure, so the answer describes what the learner was shown
            # rather than what the question later became.
            served_adaptive_eligible=question.is_adaptive_eligible,
        )

        # The AI test-case fallback that used to live here is GONE (M2 P2.5).
        #
        # It called an LLM during a learner's request and wrote the result to
        # `question.hidden_test_cases` as permanent grading truth. Nothing ever
        # executed a trusted solution against those cases, so the answer key
        # was a language model's guess — and a learner's correct solution could
        # be marked Wrong Answer because the key itself was wrong. A user
        # request must never invent the standard it is about to be judged by.
        #
        # It also made grading non-deterministic in a way no test could catch:
        # the same problem could be graded against different suites depending
        # on which request happened to arm it first.
        #
        # A problem with no hidden tests is now simply not servable.
        # `_servable_questions()` already excludes it from every recommendation
        # path, and CodeSubmitView returns 409 `question_not_gradable` if one
        # is requested by id. Arming problems is the seed/validation
        # pipeline's job, run by an operator, verified against an oracle.
        if not question.hidden_test_cases:
            logger.error(
                "Question %s (%s) has no hidden tests and is not gradable; "
                "it must be armed by the seed pipeline, not by a user request",
                question.pk, question.title,
            )

        if question.base_difficulty < 1100: diff_text = "Easy"
        elif question.base_difficulty < 1400: diff_text = "Medium"
        else: diff_text = "Hard"

        # Topic provenance (M2 P2.8a). The hierarchical route may serve a
        # different topic than the caller asked for — the DAG's whole purpose
        # is sequencing — but nothing in the response ever said so, and the
        # portal renders its topic badge from the URL. The badge could
        # therefore disagree with the question underneath it.
        #
        # Selection semantics are UNCHANGED. This reports what happened; it
        # does not alter what happens. Whether an explicit `?topic=` should
        # become a hard constraint is a product question the frontend cannot
        # currently express — it always sends the parameter, defaulting to
        # 'Array', so "chosen" and "defaulted" are indistinguishable server
        # side. Deciding that needs a frontend contract change, not a guess.
        served_topic = question.topic.name
        topic_substituted = served_topic.strip().lower() != topic_name.strip().lower()

        return Response({
            "id": str(question.pk),
            "title": f"[{question.topic.name}] {question.title}",
            "difficulty": diff_text,
            "description": question.content,
            "explanation": xai_explanation,
            "boilerplate_code": question.boilerplate_code,
            "requested_topic": topic_name,
            "served_topic": served_topic,
            "topic_substituted": topic_substituted,
            # Sample INPUT only, for the Run button. The stdin of case 1 is
            # already public in the Examples block, but its expected_output
            # must never ship: for single-case questions it IS the answer
            # key. Grading data never leaves the server.
            "sample_case": self._sample_case(question),
            "advanced_xai": advanced_data,
            # M2 P2.25. Serving still FILTERS on deliverability only, so a
            # served question may be unverified. Reported rather than hidden:
            # `servable: true` beside `adaptive_eligible: false` is the whole
            # point.
            #
            # P2.31 update: selection now PREFERS trusted questions within a
            # difficulty band, so this is more often true than it was — but
            # preference is not a guarantee, and the badge must keep telling
            # the learner what they actually got. Read from the question; this
            # line still influences nothing.
            "trust": question.trust_summary(),
            # Which languages this question can actually be attempted in
            # (M2 P2.35). Reported, never enforced here: the client uses it to
            # offer languages that work instead of listing all five and
            # letting a learner discover that the C++ starter has no main().
            #
            # `blocked` carries the reason for each refusal rather than just
            # omitting the language, because a silently shortened list is
            # indistinguishable from a language the platform dropped.
            "languages": {
                "ready": language_readiness.ready_languages(question),
                "blocked": language_readiness.blocked_languages(question),
            },
        })
    

    # Concrete techniques to drill per curriculum topic — turns the XAI
    # panel from a diagnosis into actionable advice.
    PRACTICE_TIPS = {
        "Array": "prefix sums and two-pointer sweeps",
        "String": "sliding-window substring patterns",
        "Hash Table": "hash-map lookups that replace nested loops",
        "Two Pointers": "left/right pointer convergence problems",
        "Stack": "monotonic stack problems",
        "Binary Search": "binary search on the answer space",
        "Linked List": "dummy-head and fast/slow pointer techniques",
        "Tree": "recursive DFS with clear return values",
        "Trie": "prefix-tree insert and search implementations",
        "Backtracking": "choose-explore-unchoose templates",
        "Depth-First Search": "grid flood-fill and component counting",
        "Breadth-First Search": "level-order traversal with a queue",
        "Graph": "adjacency-list building plus BFS/DFS traversal",
        "Union Find": "parent-array union/find with path compression",
        "Greedy": "sort-then-sweep with an exchange argument",
        "Divide and Conquer": "merge-sort style splitting",
        "Dynamic Programming": "1-D DP tables before 2-D ones",
        "Math": "modular arithmetic and digit manipulation",
        "Bit Manipulation": "XOR tricks and bit masking",
    }

    FACTOR_TIPS = {
        'Time Complexity': "Watch your time complexity — try replacing nested loops with hash maps or sorting.",
        'Space Complexity': "Watch your memory — reuse structures in place instead of building extra arrays.",
        'Logic Accuracy': "Slow down and trace edge cases (empty input, single element, duplicates) before submitting.",
        'Topic Recency': "You haven't practiced this topic recently — do a quick review problem before advancing.",
    }

    def _build_recommendation(self, user, dominant):
        """
        The student's weakest reviewed topics plus a concrete technique to
        drill for each. Returns (weak_topics, recommendation_text).
        """
        weak_rows = (
            UserTopicMastery.objects.filter(user=user, reviews__gte=1, accuracy__lt=0.8)
            .select_related('topic')
            .order_by('accuracy')[:3]
        )
        weak_topics = [
            {'topic': m.topic.name, 'accuracy_pct': round(m.accuracy * 100, 1)}
            for m in weak_rows
        ]

        parts = []
        if weak_topics:
            primary = weak_topics[0]
            tip = self.PRACTICE_TIPS.get(primary['topic'], "the fundamentals of this topic")
            parts.append(
                f"📌 Your weakest area is {primary['topic']} ({primary['accuracy_pct']}% accuracy) — practice {tip}."
            )
            if len(weak_topics) > 1:
                others = ", ".join(w['topic'] for w in weak_topics[1:])
                parts.append(f"Also needs work: {others}.")
        else:
            parts.append("💪 No weak topics detected yet — keep advancing through the curriculum.")

        factor_tip = self.FACTOR_TIPS.get(dominant)
        if factor_tip:
            parts.append(factor_tip)

        return weak_topics, " ".join(parts)

    def _compute_xai(self, user, topic_name, hlr_state):
        """
        The explainability payload behind /api/code/next/.

        Single path since M1/P1.1. A SHAP-over-GCN branch used to sit in
        front of this one, selected by ENABLE_SHAP_XAI. It could never run
        in production: `render.yaml` installs requirements.txt only, so
        torch, shap and the GCN artifacts were absent from the web tier,
        and the flag shipped `false`. Every response the frontend has ever
        rendered came from the heuristic below.

        The RESPONSE SCHEMA is the contract and is unchanged — the key set
        the SPA reads (`dominant_factor`, `success_probability`,
        `shap_values`, plus `weak_topics` / `recommendation` added below)
        is byte-identical to before. `shap_values` keeps its name despite
        no longer having anything to do with SHAP: renaming it would break
        AdaptiveCodingPortal's radar chart for no gain. `source` stays too,
        so a client can still tell which engine answered.
        """
        features = TensorBuilder.build_user_feature_tensor(user, topic_name)

        time_score = round(features[0] * 100, 1)
        space_score = round(features[1] * 100, 1)
        logic_score = round(features[2] * 100, 1)
        recency_score = round(features[3] * 100, 1)

        if hlr_state < 0.50:
            dominant = 'Topic Recency'
        else:
            scores = {
                'Time Complexity': time_score,
                'Space Complexity': space_score,
                'Logic Accuracy': logic_score,
                'Topic Recency': recency_score,
            }
            dominant = max(scores, key=scores.get)

        payload = {
            'source': 'heuristic',
            'dominant_factor': dominant,
            'success_probability': round(logic_score * hlr_state, 1),
            'shap_values': [
                {'subject': 'Time Complexity', 'A': time_score, 'fullMark': 100},
                {'subject': 'Space Complexity', 'A': space_score, 'fullMark': 100},
                {'subject': 'Logic Accuracy', 'A': logic_score, 'fullMark': 100},
                {'subject': 'Topic Recency', 'A': recency_score, 'fullMark': 100},
            ],
        }

        # Actionable layer, on top of whichever engine produced the payload
        weak_topics, recommendation = self._build_recommendation(user, payload['dominant_factor'])
        payload['weak_topics'] = weak_topics
        payload['recommendation'] = recommendation
        return payload

    @staticmethod
    def _sample_case(question):
        """First case's stdin only — defensive against malformed JSON rows
        (hidden_test_cases has no schema enforcement at the DB layer)."""
        cases = question.hidden_test_cases
        if isinstance(cases, list) and cases and isinstance(cases[0], dict):
            return {"stdin": cases[0].get("stdin", "")}
        return None

class CodingOnboardingView(APIView):
    """
    POST /api/code/onboard/
    Saves the topics a user already knows so the GNN skips them.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        known_topics = request.data.get('known_topics', [])
        # Learner-state mutation lives in the service under the profile
        # lock (§2.2) — onboarding's unconditional elo write would
        # otherwise race with a concurrent submission's Elo update.
        theta_guess = ProgressionService.apply_onboarding(
            user=request.user, known_topics=known_topics
        )
        return Response({"message": f"Onboarding complete! IRT Theta calibrated to {theta_guess:.2f}."})
    

class CodingPortalListView(APIView):
    """
    GET /api/coding-portals/
    Returns a list of all active global coding courses (e.g., DSA, OS).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        portals = CodingPortal.objects.filter(is_active=True)
        serializer = CodingPortalSerializer(portals, many=True)
        return Response(serializer.data)