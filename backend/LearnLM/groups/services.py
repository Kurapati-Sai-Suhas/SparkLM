"""
groups.services — grading and progression services extracted from
CodeSubmitView (frozen architecture §2.4.6: pure move, tests unchanged).

The split gives each phase of a submission a named home:

* GradingService — builds the executable (per-question DB wrapper or a
  generic language harness), fans the hidden test cases out to Judge0
  through an injected runner, and reduces the verdicts to a final status.
  The runner is injected by the caller, so the existing test seam
  (monkeypatching coding_views._run_on_judge0) keeps working unchanged.
* ProgressionService — persists the submission, closes the recommendation
  flywheel log, triggers the agentic coach, and applies every learning
  update (Elo with the repeat-solve guard, SM-2/HLR memory, mastery
  accuracy, GDCP graph decay).

Execution order is the behavioral contract: grade -> apply_submission
(one atomic, row-locked transaction per §2.2) -> coach. The coach
failure count deliberately includes the just-persisted row; the SM-2
quality scan deliberately excludes it. Network calls never happen while
locks are held: grading precedes the transaction (its result is passed
in) and the coach webhook runs after commit.

Locking model (frozen architecture §2.2): select_for_update on the
profile row first — the per-user serialization point — then on every
mastery row the submission will touch, in ascending topic-id order. The
fixed order is what precludes deadlocks.
"""

import logging
import re
from dataclasses import dataclass, field

from django.db import transaction

from common import languages
from groups import execution_contract
from .engines.agentic_coach import trigger_agentic_coach
from .engines.elo_engine import EloEngine
from .engines.hlr_engine import HLREngine
from .hybrid_router import GDCPEngine, HierarchicalEngine, IRTEngine
from .models import (
    CodeSubmission, Question, RecommendationLog, Topic,
    UserCodingProfile, UserTopicMastery,
)
from .utils import normalize_output

logger = logging.getLogger(__name__)


# ── Generic language harnesses (LeetCode style) ──────────────────────
# Used when a question has no per-question hidden_wrapper_code entry for
# the language. C++ has no generic harness — it always needs a
# per-question wrapper.

GENERIC_PYTHON_WRAPPER = """{user_code}

import sys
import json

if __name__ == '__main__':
    stdin_str = sys.stdin.read().strip()
    args = None
    try:
        parsed_input = json.loads(stdin_str)
    except Exception:
        # Whole-blob parse failed. Multi-parameter questions store one JSON
        # value per line (e.g. nums1 on line 1, nums2 on line 2) — that is
        # NOT valid single-document JSON, so try one json.loads per line
        # before giving up and treating the input as an opaque string.
        lines = [ln for ln in stdin_str.split(chr(10)) if ln.strip() != '']
        try:
            args = [json.loads(ln) for ln in lines]
            parsed_input = None
        except Exception:
            parsed_input = stdin_str

    sol = Solution()
    try:
        # Auto-detect the method name dynamically
        methods = [m for m in dir(sol) if not m.startswith('_') and callable(getattr(sol, m))]
        target_method = getattr(sol, methods[0]) if methods else sol.solve

        if args is not None:
            res = target_method(*args)
        elif isinstance(parsed_input, list):
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
        # Reported on stderr and exited non-zero (M2 P2.6). This used to print
        # to stdout and exit 0, so Judge0 returned status_id 3 "Accepted", the
        # final-status scan for ids 7-12 could never fire, and every Python
        # runtime error was recorded as wrong_answer. The submission fails
        # either way — `all_passed` is unchanged — so this corrects the LABEL
        # without altering any verdict, which is why it applies to v1 too.
        import sys as _sys
        print(f"Runtime Error: {e}", file=_sys.stderr)
        raise SystemExit(1)
"""

GENERIC_JAVA_WRAPPER = """import java.util.*;
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
            // Rethrown as an unchecked exception (M2 P2.6) so the JVM exits
            // non-zero and Judge0 classifies the run. printStackTrace alone
            // returned normally, so a crash reached the grader as an empty
            // stdout and was recorded as wrong_answer. Verdict unchanged —
            // only the status label — so this applies to v1 as well.
            throw new RuntimeException(e);
        }
    }
}

{user_code}
"""

GENERIC_JS_WRAPPER = """{user_code}

const __stdin = require('fs').readFileSync(0, 'utf8').trim();
let __parsed;
let __args = null;
try {
    __parsed = JSON.parse(__stdin);
} catch (e) {
    // Whole-blob parse failed. Multi-parameter questions store one JSON
    // value per line (nums1 on line 1, nums2 on line 2, ...) — not valid
    // single-document JSON. Try one JSON.parse per line before giving up
    // and treating the input as an opaque string.
    const __lines = __stdin.split(String.fromCharCode(10)).map(l => l.trim()).filter(l => l.length > 0);
    try {
        __args = __lines.map(l => JSON.parse(l));
    } catch (e2) {
        __parsed = __stdin;
    }
}

try {
    const __sol = new Solution();
    const __methods = Object.getOwnPropertyNames(Solution.prototype)
        .filter(m => m !== 'constructor' && typeof __sol[m] === 'function');
    const __target = __methods.length ? __sol[__methods[0]].bind(__sol) : __sol.solve.bind(__sol);

    let __res;
    if (__args !== null) {
        __res = __target(...__args);
    } else if (Array.isArray(__parsed)) {
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
    // stderr + non-zero exit (M2 P2.6): see the Python wrapper's note. This
    // printed to stdout and exited 0, so a crash was graded as wrong_answer.
    process.stderr.write(`Runtime Error: ${e.message}\\n`);
    process.exit(1);
}
"""


# The API accepts two spellings for JavaScript: LANGUAGE_IDS maps both "js"
# and "javascript" to the same Judge0 id, and the frontend's selector submits
# "js" while questions store their templates under "javascript". A
# per-question wrapper must therefore be found under either spelling —
# otherwise a submission silently falls through to the generic harness and is
# graded against the wrong scaffolding.
# Derived from common.languages (M4 Phase B). Seed data files a template
# under any accepted spelling depending on which generation produced it, so
# the lookup tries every spelling the registry knows for that language.
# Name kept: test_wrapper_contract.py imports it.
WRAPPER_LANGUAGE_ALIASES = {
    spelling: lang.spellings
    for lang in languages.REGISTRY
    for spelling in lang.spellings
}


def wrapper_for(question, lang_key):
    """
    The per-question wrapper template for a language, or None if the question
    does not carry a usable one. Empty/blank entries count as absent so a
    stray "" never produces an empty executable.
    """
    wrappers = getattr(question, "hidden_wrapper_code", None) or {}
    if not isinstance(wrappers, dict):
        return None
    for key in languages.wrapper_spellings(lang_key):
        template = wrappers.get(key)
        if isinstance(template, str) and template.strip():
            return template
    return None


class GradingUnavailable(Exception):
    """Judge0 could not grade the submission (timeout or transport failure)."""

    def __init__(self, details):
        super().__init__(details)
        self.details = details


@dataclass
class GradeResult:
    """Outcome of grading one submission against its hidden test cases."""

    # User code as it must be persisted: Java arrives import-stripped
    # because the strip happens before wrapping AND before storage — a
    # detail the extraction must not silently change.
    stored_code: str
    final_status: str
    passed: int
    total: int
    results: list = field(default_factory=list)

    @property
    def all_passed(self):
        return self.passed == self.total


def first_case_stats(results):
    """Execution stats reported by the first test case (ms, KB) or None."""
    exec_time = int(float(results[0]['time'] or 0) * 1000) if results and results[0].get('time') else None
    mem_used = results[0]['memory'] if results else None
    return exec_time, mem_used


class GradingService:
    """
    Wraps user code for execution and grades it against a question's
    hidden test cases. Stateless apart from the injected runner.
    """

    def __init__(self, runner):
        # Callable(source_code, language, stdin) -> Judge0 verdict dict.
        self._runner = runner

    def grade(self, question, language, raw_code):
        executable_code, stored_code = self._build_executable(question, language, raw_code)

        passed = 0
        results = []
        test_cases = question.hidden_test_cases

        for i, tc in enumerate(test_cases):
            # Convert literal \n in AI-generated test cases to actual newlines.
            verdict = self._runner(executable_code, language, tc.get('stdin', '').replace('\\n', '\n'))
            if "error" in verdict:
                raise GradingUnavailable(verdict["error"])

            expected = tc.get('expected_output', '').strip()

            raw_actual = verdict.get('stdout')
            if raw_actual is None:
                raw_actual = ''
            actual = raw_actual.strip()

            # Normalize line endings / trailing whitespace before comparing.
            expected_norm = normalize_output(expected)
            actual_norm = normalize_output(actual)
            logger.debug(
                "Judge0 case %d q=%s stdin=%r expected=%r actual=%r stderr=%r compile=%r",
                i + 1, question.pk, tc.get('stdin', ''), expected_norm, actual_norm,
                verdict.get('stderr'), verdict.get('compile_output'),
            )

            ok = (actual_norm == expected_norm) and verdict.get('status_id') == 3
            if ok:
                passed += 1

            results.append({
                "test_case":       i + 1,
                "passed":          ok,
                "status":          verdict.get('status'),
                # status_id is required by the final-status detection scan —
                # without it every non-pass collapses to wrong_answer and
                # TLE/compile/runtime errors are never reported correctly.
                "status_id":       verdict.get('status_id'),
                "your_output":     actual,
                "expected_output": expected,
                "time":            verdict.get('time'),
                "memory":          verdict.get('memory'),
            })

        total = len(test_cases)

        # Accurately report Judge0 status instead of defaulting to wrong_answer.
        final_status = "accepted" if passed == total else "wrong_answer"
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

        return GradeResult(
            stored_code=stored_code,
            final_status=final_status,
            passed=passed,
            total=total,
            results=results,
        )

    @staticmethod
    def _build_executable(question, language, raw_code):
        """
        Returns (executable_code, stored_code). Only the database wrapper is
        trusted for per-question harnessing; otherwise a generic harness is
        selected by language.
        """
        lang_key = language.lower()

        # Pre-process Java code to strip imports, as they cause compile
        # errors inside wrappers. The stripped source is what gets wrapped
        # AND what gets persisted, so it is returned as stored_code.
        if lang_key == "java":
            raw_code = re.sub(r'^\s*import\s+.*?;', '', raw_code, flags=re.MULTILINE)

        custom_wrapper = wrapper_for(question, lang_key)
        if custom_wrapper is not None:
            # A per-question wrapper defines its own I/O contract and is
            # therefore unaffected by the version — seed_problems.py's
            # questions are comma-separated, for instance. Checked FIRST so
            # versioning never overrides a question's own harness (M2 P2.6).
            executable_code = custom_wrapper.replace("{user_code}", raw_code)
            return executable_code, raw_code

        # No custom wrapper: the generic harness applies, and which one
        # depends on the question's declared contract.
        version = execution_contract.contract_version(question)
        if version == execution_contract.CONTRACT_V2:
            template = execution_contract.V2_WRAPPERS.get(
                languages.canonical(lang_key) or lang_key
            )
            if template is not None:
                return template.replace("{user_code}", raw_code), raw_code
            # C and C++ are self-contained under every contract: the learner
            # writes a complete program, so there is nothing to wrap.
            return raw_code, raw_code

        if lang_key == "python":
            executable_code = GENERIC_PYTHON_WRAPPER.replace("{user_code}", raw_code)
        elif lang_key == "java":
            executable_code = GENERIC_JAVA_WRAPPER.replace("{user_code}", raw_code)
        elif lang_key in ("js", "javascript"):
            executable_code = GENERIC_JS_WRAPPER.replace("{user_code}", raw_code)
        else:
            # Direct execution fallback (C++ needs a per-question
            # hidden_wrapper_code entry — there is no generic C++ harness).
            executable_code = raw_code

        return executable_code, raw_code


class ProgressionService:
    """Persistence and learning-model updates for a graded submission."""

    @staticmethod
    def apply_submission(user, question, language, difficulty, grade):
        """
        All learner-state mutation for one graded submission in a single
        transaction (§2.2): profile row locked first, then every mastery
        row that will be touched, ascending topic id. With the profile
        locked, the farming-guard check is race-free — two concurrent
        first solves serialize here and exactly one earns rating.

        Returns (submission, elo_result, profile).
        """
        all_passed = grade.all_passed

        # Pre-lock phase, reads only: GDCP penalties are a pure walk over
        # the (cached) curriculum graph and never read learner state, so
        # they are resolved before any row is locked.
        gdcp_penalties = []
        if not all_passed:
            gdcp_penalties = ProgressionService._resolve_gdcp_penalties(user, question)

        exec_time, mem_used = first_case_stats(grade.results)

        with transaction.atomic():
            # Lock 1: the profile row — the per-user serialization point.
            profile, _ = (
                UserCodingProfile.objects.select_for_update().get_or_create(user=user)
            )

            # Elo farming guard, now under the lock: a concurrent first
            # solve has either committed (visible here) or is blocked on
            # the profile lock behind us.
            already_solved = CodeSubmission.objects.filter(
                user=user, question=question, status='accepted'
            ).exists()

            submission = CodeSubmission.objects.create(
                user=user,
                question=question,
                language=language,
                code=grade.stored_code,
                status=grade.final_status,
                execution_time_ms=exec_time,
                memory_used_kb=mem_used,
            )

            # Close the data-flywheel loop for the routing classifier.
            recent_log = RecommendationLog.objects.filter(
                user=user,
                problem_id=str(question.id),
                actual_result_correct__isnull=True
            ).order_by('-created_at').first()
            if recent_log:
                recent_log.actual_result_correct = all_passed
                recent_log.save()

            profile.total_submissions += 1
            if all_passed:
                profile.successful_submissions += 1

            if all_passed and already_solved:
                # Re-solving an already-accepted problem is legitimate
                # spaced repetition (mastery/HLR still update below), but
                # it must not farm rating points.
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
            profile.save()

            # Lock 2..n: every mastery row this submission touches — the
            # question's own topic plus any GDCP descendants — in
            # ascending topic-id order (§2.2's deadlock preclusion).
            touched_topics = {question.topic.pk: question.topic}
            for desc_topic, _penalty in gdcp_penalties:
                touched_topics.setdefault(desc_topic.pk, desc_topic)

            masteries = {}
            for topic_pk in sorted(touched_topics):
                masteries[topic_pk], _ = (
                    UserTopicMastery.objects.select_for_update().get_or_create(
                        user=user, topic=touched_topics[topic_pk]
                    )
                )

            ProgressionService._apply_sm2_update(
                user, question, grade, submission, masteries[question.topic.pk]
            )

            # GDCP application is best-effort enrichment, as it was before
            # the single-transaction change: a failure rolls back only this
            # savepoint, never the submission itself.
            if gdcp_penalties:
                try:
                    with transaction.atomic():
                        for desc_topic, penalty in gdcp_penalties:
                            desc_mastery = masteries[desc_topic.pk]
                            desc_mastery.accuracy = max(0.0, desc_mastery.accuracy - penalty)
                            desc_mastery.save(update_fields=['accuracy'])
                except Exception:
                    logger.exception(
                        "GDCP decay failed user=%s topic=%s", user.id, question.topic.name
                    )

        return submission, elo_result, profile

    @staticmethod
    def _resolve_gdcp_penalties(user, question):
        """
        Graph-Decay Cross-Pollination: failing a topic penalizes its
        downstream dependencies. Resolves the penalties to concrete Topic
        rows using reads only (safe pre-lock). Mirrors the legacy
        resilience — any failure logs and decays nothing.
        """
        try:
            portal_name = "DSA Masterclass"
            if question.topic.portal:
                portal_name = question.topic.portal.name

            graph = HierarchicalEngine._get_graph(portal_name)
            penalties = GDCPEngine.propagate_decay(graph, question.topic.name, base_decay=0.1)
            resolved = []
            for desc_node, penalty in penalties.items():
                desc_topic = Topic.objects.filter(name=desc_node).first()
                if desc_topic:
                    resolved.append((desc_topic, penalty))
            return resolved
        except Exception:
            logger.exception(
                "GDCP decay failed user=%s topic=%s", user.id, question.topic.name
            )
            return []

    @staticmethod
    def _apply_sm2_update(user, question, grade, submission, mastery):
        """
        SM-2 spaced repetition on the (locked) mastery row: quality comes
        from the failures immediately preceding this attempt (consecutive,
        newest first). Lifetime counting would permanently cap quality at
        3 after two historic fails, no matter how cleanly later reviews go.
        """
        quality = 0
        if grade.all_passed:
            prior_statuses = CodeSubmission.objects.filter(
                user=user, question=question
            ).exclude(pk=submission.pk).order_by('-submitted_at').values_list('status', flat=True)[:10]

            recent_fails = 0
            for sub_status in prior_statuses:
                if sub_status != 'accepted':
                    recent_fails += 1
                else:
                    break

            if recent_fails == 0:
                quality = 5
            elif recent_fails == 1:
                quality = 4
            else:
                quality = 3

        new_halflife = HLREngine.update_halflife(quality, mastery.hlr_halflife)
        mastery.hlr_halflife = max(0.1, min(100.0, new_halflife))  # bound halflife

        # Bound accuracy strictly between 0 and 1.
        new_acc = (mastery.accuracy * mastery.reviews + (1.0 if grade.all_passed else 0.0)) / (mastery.reviews + 1)
        mastery.accuracy = max(0.0, min(1.0, new_acc))
        mastery.reviews += 1
        mastery.save()

        # A real submission ends any inactivity window: move last_practiced
        # forward and reset the decay checkpoint (FIX-05 support).
        EloEngine.record_real_submission(mastery)

    @staticmethod
    def apply_onboarding(user, known_topics):
        """
        Cold-start calibration (§2.2 applies here too: the unconditional
        elo write below would otherwise race with a concurrent submission's
        Elo update and clobber it). Synthetic accepted submissions mark
        known topics so the recommenders skip them. Returns the calibrated
        theta.
        """
        with transaction.atomic():
            profile, _ = (
                UserCodingProfile.objects.select_for_update().get_or_create(user=user)
            )

            for topic_name in known_topics:
                try:
                    # Per-topic savepoint keeps one bad topic from failing
                    # the whole onboarding, as in the legacy loop.
                    with transaction.atomic():
                        question = Question.objects.filter(
                            topic__name__iexact=topic_name
                        ).first()
                        if question:
                            already_accepted = CodeSubmission.objects.filter(
                                user=user, question=question, status='accepted'
                            ).exists()
                            if not already_accepted:
                                CodeSubmission.objects.create(
                                    user=user,
                                    question=question,
                                    language='python',
                                    code='# Skipped via Onboarding',
                                    status='accepted',
                                    execution_time_ms=10,
                                    memory_used_kb=1024,
                                )
                except Exception:
                    logger.exception("Error onboarding topic %s", topic_name)

            # 3-Parameter IRT cold-start calibration, assuming a diagnostic
            # test of 10 average questions: maps 0-10 known topics onto the
            # -2.0..2.0 theta range.
            num_known = len(known_topics)
            theta_guess = (num_known / 10.0) * 4.0 - 2.0
            theta_guess = max(-4.0, min(4.0, theta_guess))

            # Sanity-check the calibration against the 3PL IRT curve.
            expected = IRTEngine.expected_score(theta=theta_guess, a=1.0, b=0.0, c=0.2)
            logger.info(
                "Onboarding IRT calibration: theta=%.2f expected_score=%.2f",
                theta_guess, expected,
            )

            profile.irt_latent_logic = theta_guess
            profile.irt_latent_syntax = theta_guess
            profile.irt_latent_optimization = theta_guess

            # Also boost Elo to skip the cold-start problem.
            profile.elo_rating = 1200 + (num_known * 50)
            profile.save()

        return theta_guess

    @staticmethod
    def coach_hint(user, problem_id, question, grade):
        """
        Agentic coach trigger: escalating hint after 3+ consecutive
        failures on this question, None otherwise.
        """
        if grade.all_passed:
            return None

        # Count consecutive failures on this question, newest first,
        # stopping at the last accepted submission. The current failed
        # submission is already persisted, so it's included in the scan.
        # Scanning 15 back keeps the 5-fail (pseudocode) and 7-fail
        # (worked example) escalation tiers reachable.
        recent_statuses = CodeSubmission.objects.filter(
            user=user, question=question
        ).order_by('-submitted_at').values_list('status', flat=True)[:15]

        failed_count = 0
        for sub_status in recent_statuses:
            if sub_status != 'accepted':
                failed_count += 1
            else:
                break

        if failed_count < 3:
            return None

        return trigger_agentic_coach(
            user=user,
            problem_id=problem_id,
            code_snippet=grade.stored_code,
            error_logs=str(grade.results),
            failed_attempts=failed_count
        )
