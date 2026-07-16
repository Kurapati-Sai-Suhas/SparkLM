import os
import base64
import requests
import logging
from django.db.models import F, Func
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle

# Import Models
from .models import (
    CodingPortal, Topic, Question, UserCodingProfile, CodeSubmission, UserBadge, Badge,
    UserTopicMastery, AgenticCoachLog, RecommendationLog
)
from .serializers import CodeSubmitSerializer, CodingPortalSerializer
from .models import CodingPortal

# Import AI Engines & Services
from .hybrid_router import (
    HierarchicalEngine,
    compute_routing_telemetry, get_mastered_topic_names,
)
from .services import GradingService, GradingUnavailable, ProgressionService
from .ai_services import generate_test_cases
from .engines.tensor_builder import TensorBuilder
USE_REAL_SHAP = os.environ.get('ENABLE_SHAP_XAI', 'false') == 'true'


logger = logging.getLogger(__name__)


LANGUAGE_IDS = {
    "python":     71,
    "java":       62,
    "cpp":        54,
    "c":          50,
    "js":         63,
    # The serializer validates 'javascript' but this map only had 'js',
    # so every JS submission errored with "Unsupported language".
    "javascript": 63,
}

# Host and base URL are config-driven; the host header MUST match the
# RapidAPI product the key is registered for (settings.py previously
# defaulted to judge0-extra-ce while this file hardcoded judge0-ce).
JUDGE0_HOST = os.environ.get('JUDGE0_API_HOST', 'judge0-ce.p.rapidapi.com')
JUDGE0_BASE = os.environ.get('JUDGE0_URL', f'https://{JUDGE0_HOST}')
JUDGE0_KEY  = os.environ.get('JUDGE0_API_KEY')

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

        # 2. Get global leaderboard (Top 3 by Elo)
        top_profiles = UserCodingProfile.objects.all().order_by('-elo_rating')[:3]
        leaderboard = []
        for idx, p in enumerate(top_profiles):
            leaderboard.append({
                "rank": idx + 1,
                "name": p.user.get_full_name() or p.user.username,
                "handle": f"@{p.user.username}",
                "elo": int(p.elo_rating)
            })

        # 3. Get recent badges
        recent_badges = UserBadge.objects.filter(user=request.user).order_by('-awarded_at')[:3]
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'judge0'

    def post(self, request):
        raw_code = request.data.get('code', '').strip()
        language = request.data.get('language', 'python')
        stdin    = request.data.get('stdin', '')
        problem_id = request.data.get('problem_id', None)
        
        if not raw_code:
            return Response({"error": "code is required"}, status=400)
            
        executable_code = raw_code
        lang_key = language.lower()

        # Dynamic Wrapper Injection for Code Run
        if problem_id:
            try:
                question = Question.objects.get(id=problem_id)
                if question.hidden_wrapper_code and lang_key in question.hidden_wrapper_code:
                    wrapper_template = question.hidden_wrapper_code[lang_key]
                    executable_code = wrapper_template.replace("{user_code}", raw_code)
            except Question.DoesNotExist:
                pass

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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'judge0'

    def post(self, request):
        serializer = CodeSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clean = serializer.validated_data

        raw_code   = clean['code']
        language   = clean['language']
        problem_id = clean['problem_id']

        try:
            question = Question.objects.get(id=problem_id)
        except (Question.DoesNotExist, ValueError):
            return Response({"error": "Question not found"}, status=404)
        if not question.hidden_test_cases:
            return Response({"error": "Question misconfigured: no test cases"}, status=500)

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

        return Response({
            "submission_id": submission.id,
            "status":        grade.final_status,
            "passed":        grade.passed,
            "total":         grade.total,
            "all_passed":    grade.all_passed,
            "test_results":  grade.results,
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'recommend'

    def get(self, request):
        from .hybrid_router import RoutingClassifier
        profile, _ = UserCodingProfile.objects.get_or_create(user=request.user)
        target_elo = profile.elo_rating
        
        # Default to 'Array' if no topic is provided by the frontend
        topic_name = request.query_params.get('topic', 'Array')
        
        try:
            topic = Topic.objects.get(name__iexact=topic_name)
        except Topic.DoesNotExist:
            topic = Topic.objects.first() # Fallback

        # --- Safely cast problem IDs to Integers ---
        raw_solved_ids = CodeSubmission.objects.filter(
            user=request.user, status='accepted'
        ).values_list('question_id', flat=True)

        solved_ids = []
        for pid in raw_solved_ids:
            try:
                solved_ids.append(int(pid))
            except (ValueError, TypeError):
                pass 
        # -------------------------------------------

        question = None
        xai_explanation = ""
        advanced_data = None 

        # 🚀 ADVANCED ML: Sequence-Based LSTM (Deep Knowledge Tracing)
        # (Disabled for production - experimental code removed to reduce cold starts and memory usage)

        # 🚥 ML-BASED TRAFFIC COP
        router = RoutingClassifier()
        # FR-RTR-01 v2: mean + runs-test streakiness over the last 20
        # submissions (frozen architecture §6.2).
        avg_acc, runs_z, sample_size = compute_routing_telemetry(request.user)
        route_decision = router.predict_route(avg_acc, runs_z, target_elo / 2000.0)
        logger.info(
            "[Traffic Cop] user=%s route=%s (avg_acc=%.2f runs_z=%.2f n=%d elo=%.0f)",
            request.user.id, route_decision, avg_acc, runs_z, sample_size, target_elo,
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

            question = Question.objects.filter(topic__name=target_topic_name).exclude(id__in=solved_ids).exclude(
                content__icontains=Question.PLACEHOLDER_MARKER  # unseeded placeholders are not servable
            ).annotate(
                elo_diff=Func(F('base_difficulty') - target_elo, function='ABS')
            ).order_by('elo_diff').first()

        # 🚥 ROUTE 2: FLAT (ELO ENGINE)
        else:
            logger.info("[Traffic Cop] Routing to Flat Elo engine for %s", topic.name if topic else "fallback")
            xai_explanation = f"📈 Matched to your current skill level (Elo: {round(target_elo)})."

            if topic:
                question = Question.objects.filter(topic__name=topic.name).exclude(id__in=solved_ids).exclude(
                    content__icontains=Question.PLACEHOLDER_MARKER
                ).annotate(
                    elo_diff=Func(F('base_difficulty') - target_elo, function='ABS')
                ).order_by('elo_diff').first()

        if not question:
            unsolved_qs = Question.objects.exclude(id__in=solved_ids).exclude(
                content__icontains=Question.PLACEHOLDER_MARKER
            )
            if not unsolved_qs.exists():
                return Response({
                    'status': 'completed',
                    'message': 'You have solved all available problems! New problems coming soon.',
                    'mastery_percentage': 100.0,
                    'next_problem': None,
                }, status=200)
            question = unsolved_qs.first()

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
            problem_id=str(question.pk)
        )

        # 🤖 AI TEST CASE GENERATION FALLBACK
        if not question.hidden_test_cases:
            generated_cases = generate_test_cases(question.title, question.content)
            if generated_cases:
                question.hidden_test_cases = generated_cases
                question.save(update_fields=['hidden_test_cases'])
            else:
                # Never persist a failed generation — serve the problem
                # without hidden tests and let a later request retry.
                logger.error(
                    "Test-case generation failed for question %s (%s); serving without hidden tests",
                    question.pk, question.title,
                )

        if question.base_difficulty < 1100: diff_text = "Easy"
        elif question.base_difficulty < 1400: diff_text = "Medium"
        else: diff_text = "Hard"

        return Response({
            "id": str(question.pk),
            "title": f"[{question.topic.name}] {question.title}",
            "difficulty": diff_text,
            "description": question.content, 
            "explanation": xai_explanation,
            "boilerplate_code": question.boilerplate_code,
            # Sample INPUT only, for the Run button. The stdin of case 1 is
            # already public in the Examples block, but its expected_output
            # must never ship: for single-case questions it IS the answer
            # key. Grading data never leaves the server.
            "sample_case": self._sample_case(question),
            "advanced_xai": advanced_data
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
        features = TensorBuilder.build_user_feature_tensor(user, topic_name)

        payload = None
        if USE_REAL_SHAP:
            # None means SHAP unavailable/failed — fall through to the
            # heuristic so the endpoint never 500s over explainability.
            payload = self._compute_shap_xai(features, hlr_state)

        if payload is None:
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

    def _compute_shap_xai(self, features, hlr_state):
        """
        Real SHAP attributions over the trained GCN, normalized to the SAME
        schema the frontend reads for the heuristic path (shap_values /
        dominant_factor / success_probability). Returns None on any failure.
        """
        try:
            import torch
            from .engines.shap_explainer import get_xai_engine

            engine = get_xai_engine()
            if engine is None:
                return None

            user_tensor = torch.tensor(features, dtype=torch.float32)
            result = engine.generate_radar_data(user_tensor)
            if result is None:
                return None  # degenerate attributions — use heuristic
            success_prob = engine.predict_success(user_tensor)

            # Same memory-atrophy override as the heuristic path.
            dominant = 'Topic Recency' if hlr_state < 0.50 else result['dominant_factor']

            return {
                'source': 'shap',
                'dominant_factor': dominant,
                'success_probability': round(success_prob * 100, 1),
                'shap_values': result['radar_data'],
                'insight_text': result['insight_text'],
            }
        except Exception:
            logger.exception("SHAP XAI computation failed; using heuristic payload")
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