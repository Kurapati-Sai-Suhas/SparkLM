import os
import base64
import requests
import logging
from django.db import transaction
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
from .engines.elo_engine import EloEngine
from .engines.mirt_engine import MIRTEngine
from .hybrid_router import (
    GDCPEngine, HierarchicalEngine, IRTEngine,
    compute_routing_telemetry, get_mastered_topic_names,
)
# import torch
from .engines.agentic_coach import trigger_agentic_coach
from .ai_services import generate_test_cases
from .utils import normalize_output
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
            
        # Fallback if no badges (for UI showcase)
        if not badges:
            badges = [
                {"id": "b1", "name": "First Steps", "description": "Joined LearnLM", "color": "primary", "icon": "Award"}
            ]

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
        test_cases = clean.get('test_cases', [])

        # 🚀 Fetch the Question from DB
        try:
            question = Question.objects.get(id=problem_id)
            difficulty = question.base_difficulty
            test_cases = question.hidden_test_cases
            if not test_cases:
                return Response({"error": "Question misconfigured: no test cases"}, status=500)
        except (Question.DoesNotExist, ValueError):
            return Response({"error": "Question not found"}, status=404)

        # ---------------------------------------------------------
        # 🚀 THE STRICT DATABASE WRAPPER ENGINE
        # ---------------------------------------------------------
        executable_code = raw_code
        lang_key = language.lower()

        # Pre-process Java code to strip imports, as they cause compile errors inside wrappers
        if lang_key == "java":
            import re
            raw_code = re.sub(r'^\s*import\s+.*?;', '', raw_code, flags=re.MULTILINE)

        # We ONLY use the wrapper from the database now. No hardcoding.
        if question.hidden_wrapper_code and lang_key in question.hidden_wrapper_code:
            wrapper_template = question.hidden_wrapper_code[lang_key]
            executable_code = wrapper_template.replace("{user_code}", raw_code)
        elif lang_key == "python":
            # 🚀 DYNAMIC GENERIC WRAPPER FOR PYTHON (Leetcode Style)
            generic_python_wrapper = """{user_code}

import sys
import json

if __name__ == '__main__':
    stdin_str = sys.stdin.read().strip()
    try:
        parsed_input = json.loads(stdin_str)
    except:
        parsed_input = stdin_str
        
    sol = Solution()
    try:
        # Auto-detect the method name dynamically
        methods = [m for m in dir(sol) if not m.startswith('_') and callable(getattr(sol, m))]
        target_method = getattr(sol, methods[0]) if methods else sol.solve
        
        if isinstance(parsed_input, list):
            res = target_method(*parsed_input) if type(parsed_input) is list else target_method(parsed_input)
        elif isinstance(parsed_input, dict):
            res = target_method(**parsed_input)
        else:
            res = target_method(parsed_input)
            
        if isinstance(res, (list, dict)):
            print(json.dumps(res).replace(" ", ""))
        elif isinstance(res, bool):
            print(str(res).lower())
        else:
            print(str(res))
    except Exception as e:
        print(f"Runtime Error: {e}")
"""
            executable_code = generic_python_wrapper.replace("{user_code}", raw_code)
        elif lang_key == "java":
            # 🚀 DYNAMIC GENERIC WRAPPER FOR JAVA (Leetcode Style using Reflection)
            
            generic_java_wrapper = """import java.util.*;
import java.lang.reflect.*;

public class Main {
    public static void main(String[] args) {
        Scanner scanner = new Scanner(System.in);
        StringBuilder sb = new StringBuilder();
        while (scanner.hasNextLine()) {
            sb.append(scanner.nextLine()).append("\\n");
        }
        String input = sb.toString().trim();
        
        try {
            Solution sol = new Solution();
            Method[] methods = Solution.class.getDeclaredMethods();
            Method targetMethod = null;
            for (Method m : methods) {
                if (Modifier.isPublic(m.getModifiers()) && !m.getDeclaringClass().equals(Object.class)) {
                    targetMethod = m;
                    break;
                }
            }
            
            if (targetMethod == null) {
                System.out.println("Error: No public method found in Solution class.");
                return;
            }
            
            Class<?>[] paramTypes = targetMethod.getParameterTypes();
            Object[] argsToPass = new Object[paramTypes.length];
            
            String[] inputs = input.split("\\\\n");
            for (int i = 0; i < paramTypes.length && i < inputs.length; i++) {
                Class<?> pType = paramTypes[i];
                String val = inputs[i].trim();
                if (pType == int.class || pType == Integer.class) {
                    argsToPass[i] = Integer.parseInt(val);
                } else if (pType == int[].class) {
                    String clean = val.replace("[", "").replace("]", "").trim();
                    if (clean.isEmpty()) {
                        argsToPass[i] = new int[0];
                    } else {
                        String[] parts = clean.split("[, ]+");
                        int[] arr = new int[parts.length];
                        for(int j=0; j<parts.length; j++) arr[j] = Integer.parseInt(parts[j].trim());
                        argsToPass[i] = arr;
                    }
                } else if (pType == double.class || pType == Double.class) {
                    argsToPass[i] = Double.parseDouble(val);
                } else if (pType == boolean.class || pType == Boolean.class) {
                    argsToPass[i] = Boolean.parseBoolean(val);
                } else {
                    argsToPass[i] = val;
                }
            }
            Object result;
            if (targetMethod.isVarArgs()) {
                result = targetMethod.invoke(sol, new Object[]{argsToPass});
            } else {
                result = targetMethod.invoke(sol, argsToPass);
            }
            if (result != null) {
                if (result instanceof int[]) {
                    int[] res = (int[])result;
                    for(int j=0; j<res.length; j++) System.out.print(res[j] + (j == res.length-1 ? "" : " "));
                    System.out.println();
                } else if (result instanceof double[]) {
                    double[] res = (double[])result;
                    for(int j=0; j<res.length; j++) System.out.print(res[j] + (j == res.length-1 ? "" : " "));
                    System.out.println();
                } else if (result instanceof Object[]) {
                    Object[] res = (Object[])result;
                    for(int j=0; j<res.length; j++) System.out.print(res[j] + (j == res.length-1 ? "" : " "));
                    System.out.println();
                } else {
                    System.out.println(result.toString().trim());
                }
            }
            
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}

{user_code}
"""
            executable_code = generic_java_wrapper.replace("{user_code}", raw_code)
        elif lang_key in ("js", "javascript"):
            # 🚀 DYNAMIC GENERIC WRAPPER FOR JAVASCRIPT (mirrors the Python one:
            # whole stdin JSON-parsed if possible, else passed as one raw string)
            generic_js_wrapper = """{user_code}

const __stdin = require('fs').readFileSync(0, 'utf8').trim();
let __parsed;
try { __parsed = JSON.parse(__stdin); } catch (e) { __parsed = __stdin; }

try {
    const __sol = new Solution();
    const __methods = Object.getOwnPropertyNames(Solution.prototype)
        .filter(m => m !== 'constructor' && typeof __sol[m] === 'function');
    const __target = __methods.length ? __sol[__methods[0]].bind(__sol) : __sol.solve.bind(__sol);

    let __res;
    if (Array.isArray(__parsed)) {
        __res = __target(...__parsed);
    } else {
        __res = __target(__parsed);
    }

    if (Array.isArray(__res) || (__res !== null && typeof __res === 'object')) {
        console.log(JSON.stringify(__res).replace(/ /g, ''));
    } else if (typeof __res === 'boolean') {
        console.log(String(__res));
    } else {
        console.log(String(__res));
    }
} catch (e) {
    console.log(`Runtime Error: ${e.message}`);
}
"""
            executable_code = generic_js_wrapper.replace("{user_code}", raw_code)
        else:
            # Fallback to direct execution (C++ needs a per-question
            # hidden_wrapper_code entry — there is no generic C++ harness)
            executable_code = raw_code
        # ---------------------------------------------------------

        passed  = 0
        results = []

        # 1. Run all test cases USING THE WRAPPED CODE
        for i, tc in enumerate(test_cases):
            # 🚀 AI FIX: Convert literal \\n in AI-generated test cases to actual newlines
            verdict  = _run_on_judge0(executable_code, language, tc.get('stdin', '').replace('\\n', '\n'))
            if "error" in verdict:
                return Response({"error": "Code Execution Service Unavailable", "details": verdict["error"]}, status=503)

            expected = tc.get('expected_output', '').strip()

            # If stdout is None, fallback to empty string
            raw_actual = verdict.get('stdout')
            if raw_actual is None:
                raw_actual = ''
            actual = raw_actual.strip()

            # Normalize line endings / trailing whitespace before comparing
            expected_norm = normalize_output(expected)
            actual_norm = normalize_output(actual)
            logger.debug(
                "Judge0 case %d q=%s stdin=%r expected=%r actual=%r stderr=%r compile=%r",
                i + 1, question.pk, tc.get('stdin', ''), expected_norm, actual_norm,
                verdict.get('stderr'), verdict.get('compile_output'),
            )

            ok       = (actual_norm == expected_norm) and verdict.get('status_id') == 3

            if ok:
                passed += 1
                
            results.append({
                "test_case":       i + 1,
                "passed":          ok,
                "status":          verdict.get('status'),
                # status_id is required by the final-status detection loop
                # below — without it every non-pass collapsed to
                # wrong_answer and TLE/compile/runtime errors were never
                # reported (or fed to the MIRT/quality engines) correctly.
                "status_id":       verdict.get('status_id'),
                "your_output":     actual,
                "expected_output": expected,
                "time":            verdict.get('time'),
                "memory":          verdict.get('memory'),
            })

        total      = len(test_cases)
        all_passed = passed == total
        
        # 🚀 AI FIX: Accurately report Judge0 status instead of defaulting to wrong_answer
        final_status = "accepted" if all_passed else "wrong_answer"
        for v in results:
            status_id = v.get("status_id")
            if status_id == 5:
                final_status = "time_limit"
                break
            elif status_id == 6:
                final_status = "compile_error"
                break
            elif status_id in [7, 8, 9, 10, 11, 12]:
                final_status = "runtime_error"
                break

        # Elo farming guard: check for a prior accepted solve BEFORE the
        # new submission row is created.
        already_solved = CodeSubmission.objects.filter(
            user=request.user, question=question, status='accepted'
        ).exists()

        # 2. Log submission to the database and update metrics transactionally
        with transaction.atomic():
            submission = CodeSubmission.objects.create(
                user=request.user,
                question=question,
                language=language,
                code=raw_code,
                status=final_status,
                execution_time_ms=int(float(results[0]['time'] or 0) * 1000) if results and results[0].get('time') else None,
                memory_used_kb=results[0]['memory'] if results else None,
            )

            # 🚀 UPDATE THE DATA FLYWHEEL LOG
            recent_log = RecommendationLog.objects.filter(
                user=request.user, 
                problem_id=str(question.id), 
                actual_result_correct__isnull=True
            ).order_by('-created_at').first()
            
            if recent_log:
                recent_log.actual_result_correct = all_passed
                recent_log.save()

        # 🚀 AGENTIC COACH TRIGGER (3+ consecutive failures, escalating tiers)
        agentic_hint = None
        if not all_passed:
            # Count consecutive failures on this question, newest first,
            # stopping at the last accepted submission. The current failed
            # submission is already persisted, so it's included in the scan.
            # Scanning 15 back keeps the 5-fail (pseudocode) and 7-fail
            # (worked example) escalation tiers reachable — the old [1:3]
            # slice capped the count at 3, so those tiers could never fire.
            recent_statuses = CodeSubmission.objects.filter(
                user=request.user, question=question
            ).order_by('-submitted_at').values_list('status', flat=True)[:15]

            failed_count = 0
            for sub_status in recent_statuses:
                if sub_status != 'accepted':
                    failed_count += 1
                else:
                    break

            if failed_count >= 3:
                agentic_hint = trigger_agentic_coach(
                    user=request.user,
                    problem_id=problem_id,
                    code_snippet=raw_code,
                    error_logs=str(results),
                    failed_attempts=failed_count
                )

        # 3. Update User's Profile Stats & Elo Rating
        profile, _ = UserCodingProfile.objects.get_or_create(user=request.user)
        profile.total_submissions += 1
        
        if all_passed:
            profile.successful_submissions += 1

        exec_time = int(float(results[0]['time'] or 0) * 1000) if results and results[0].get('time') else None
        mem_used = results[0]['memory'] if results else None

        if all_passed and already_solved:
            # Re-solving an already-accepted problem is legitimate spaced
            # repetition (mastery/HLR still update below), but it must not
            # farm rating points.
            elo_result = {
                "old_rating": round(profile.elo_rating, 2),
                "new_rating": round(profile.elo_rating, 2),
                "rating_change": 0.0,
                "insight": "✅ Solved again! Repeat solves keep your memory fresh but don't change your rating.",
            }
        else:
            elo_result = EloEngine.calculate_new_rating(
                user_rating=profile.elo_rating,
                question_difficulty=difficulty,
                is_correct=all_passed,
                execution_time_ms=exec_time,
                memory_used_kb=mem_used
            )

        profile.elo_rating = elo_result["new_rating"]

        # 🚀 ADVANCED ML: Multi-dimensional IRT (MIRT) (Experimental, disabled schema update for now)
        # from .engines.mirt_engine import MIRTEngine
        # mirt_update = MIRTEngine.update_latents(...)

        profile.save()

        # 4. 🚀 ADVANCED ML: SM-2 Spaced Repetition & GDCP Graph Decay
        from .models import UserTopicMastery
        mastery, _ = UserTopicMastery.objects.get_or_create(
            user=request.user,
            topic=question.topic
        )
        
        # Calculate SM-2 Quality from the failures immediately preceding
        # this attempt (consecutive, newest first). The old version counted
        # lifetime failures for the question, which permanently capped
        # quality at 3 after two historic fails, no matter how cleanly the
        # user solves it on later reviews.
        quality = 0
        if all_passed:
            prior_statuses = CodeSubmission.objects.filter(
                user=request.user, question=question
            ).exclude(pk=submission.pk).order_by('-submitted_at').values_list('status', flat=True)[:10]

            recent_fails = 0
            for sub_status in prior_statuses:
                if sub_status != 'accepted':
                    recent_fails += 1
                else:
                    break

            if recent_fails == 0: quality = 5
            elif recent_fails == 1: quality = 4
            else: quality = 3
            
        from .engines.hlr_engine import HLREngine
        new_halflife = HLREngine.update_halflife(quality, mastery.hlr_halflife)
        mastery.hlr_halflife = max(0.1, min(100.0, new_halflife)) # Bound halflife
        
        # Bound accuracy strictly between 0 and 1
        new_acc = (mastery.accuracy * mastery.reviews + (1.0 if all_passed else 0.0)) / (mastery.reviews + 1)
        mastery.accuracy = max(0.0, min(1.0, new_acc))
        mastery.reviews += 1
        mastery.save()

        # A real submission ends any inactivity window: move last_practiced
        # forward and reset the decay checkpoint (FIX-05 support).
        EloEngine.record_real_submission(mastery)

        # GDCP: Graph-Decay Cross-Pollination (If failed, penalize downstream dependencies)
        if not all_passed:
            try:
                portal_name = "DSA Masterclass"
                if question.topic.portal:
                    portal_name = question.topic.portal.name
                
                graph = HierarchicalEngine._get_graph(portal_name)
                penalties = GDCPEngine.propagate_decay(graph, question.topic.name, base_decay=0.1) 
                for desc_node, penalty in penalties.items():
                    desc_topic = Topic.objects.filter(name=desc_node).first()
                    if desc_topic:
                        desc_mastery, _ = UserTopicMastery.objects.get_or_create(user=request.user, topic=desc_topic)
                        desc_mastery.accuracy = max(0.0, desc_mastery.accuracy - penalty)
                        desc_mastery.save(update_fields=['accuracy'])
            except Exception as e:
                logger.exception("GDCP decay failed user=%s topic=%s", request.user.id, question.topic.name)

        # 5. Return data to React frontend
        return Response({
            "submission_id": submission.id,
            "status":        final_status,
            "passed":        passed,
            "total":         total,
            "all_passed":    all_passed,
            "test_results":  results,
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
        # FR-RTR-01: mean + variance of correctness over the last 20 submissions.
        avg_acc, var_acc, sample_size = compute_routing_telemetry(request.user)
        route_decision = router.predict_route(avg_acc, var_acc, target_elo / 2000.0)
        logger.info(
            "[Traffic Cop] user=%s route=%s (avg_acc=%.2f var_acc=%.3f n=%d elo=%.0f)",
            request.user.id, route_decision, avg_acc, var_acc, sample_size, target_elo,
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
            "hiddenTestCases": question.hidden_test_cases,
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
        
        for topic_name in known_topics:
            try:
                question = Question.objects.filter(topic__name__iexact=topic_name).first()
                if question:
                    already_accepted = CodeSubmission.objects.filter(
                        user=request.user, question=question, status='accepted'
                    ).exists()
                    if not already_accepted:
                        CodeSubmission.objects.create(
                            user=request.user,
                            question=question,
                            language='python',
                            code='# Skipped via Onboarding',
                            status='accepted',
                            execution_time_ms=10,
                            memory_used_kb=1024,
                        )
            except Exception:
                logger.exception("Error onboarding topic %s", topic_name)

        # 🚀 ADVANCED ML: 3-Parameter IRT Cold Start Calibration
        profile, _ = UserCodingProfile.objects.get_or_create(user=request.user)
        # Assuming a diagnostic test of 10 average questions
        num_known = len(known_topics)
        theta_guess = (num_known / 10.0) * 4.0 - 2.0 # Maps 0-10 scale to -2.0 to 2.0 theta range
        theta_guess = max(-4.0, min(4.0, theta_guess))

        # Sanity-check the calibration against the 3PL IRT curve
        expected = IRTEngine.expected_score(theta=theta_guess, a=1.0, b=0.0, c=0.2)
        logger.info("Onboarding IRT calibration: theta=%.2f expected_score=%.2f", theta_guess, expected)

        profile.irt_latent_logic = theta_guess
        profile.irt_latent_syntax = theta_guess
        profile.irt_latent_optimization = theta_guess

        # Also boost Elo to skip "Cold Start" problem
        profile.elo_rating = 1200 + (num_known * 50)
        profile.save()

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