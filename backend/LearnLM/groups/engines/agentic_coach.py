"""
groups/engines/agentic_coach.py

Fixes applied:
- FIX-06 (HIGH / BUG-05): The bare `except: pass` after requests.post()
  silently swallowed every possible failure mode (timeout, connection
  error, bad HTTP status, malformed JSON), leaving zero trace when the
  n8n webhook pipeline broke. Now each failure mode is caught
  separately, logged with context, and recorded on the log row so you
  can actually debug the coach pipeline.
"""

import os
import time
import logging
import requests
from groups.models import AgenticCoachLog

logger = logging.getLogger(__name__)


def _get_fallback_hint(failed_attempts: int) -> str:
    if failed_attempts < 5:
        return ("🧠 *Agentic Coach Insight (Nudge)*: I noticed you're struggling with the loop "
                "bounds. Try tracing the variables on a piece of paper for a small input like [1, 2].")
    elif failed_attempts < 7:
        return ("🧠 *Agentic Coach Insight (Pseudocode)*: Here is the structure you need:\n"
                "1. Initialize a pointer at 0.\n"
                "2. Loop through the array.\n"
                "3. If condition met, swap and increment pointer.")
    else:
        return ("🧠 *Agentic Coach Insight (Worked Example)*: Let's walk through the exact "
                "solution. We need to maintain two pointers. Here is a similar example showing "
                "how to structure your `while` loop...")


def trigger_agentic_coach(user, problem_id, code_snippet, error_logs, failed_attempts):
    """
    Triggers the n8n webhook that runs the Gemini AI agent.
    If N8N_WEBHOOK_URL is not set, or the webhook fails for any reason,
    gracefully falls back to a mock Socratic hint — but now every
    failure mode is logged instead of silently swallowed.
    """
    webhook_url = os.environ.get('N8N_WEBHOOK_URL')

    if failed_attempts < 5:
        context = "Student has failed 3 times. Generate a Socratic conceptual nudge without giving the exact answer."
    elif failed_attempts < 7:
        context = "Student has failed 5 times. Give them the pseudocode structure for this algorithm."
    else:
        context = "Student has failed 7+ times. Give them a worked example with a full explanation."

    payload = {
        "user_id": user.id,
        "username": user.username,
        "problem_id": problem_id,
        "failed_attempts": failed_attempts,
        "code_snippet": code_snippet,
        "error_logs": error_logs,
        "context": context
    }

    mocked_hint = ""
    hint_source = "fallback"
    webhook_latency_ms = None

    if webhook_url:
        start = time.monotonic()
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            webhook_latency_ms = round((time.monotonic() - start) * 1000, 2)
            response.raise_for_status()  # raises HTTPError on 4xx/5xx
            n8n_data = response.json()
            mocked_hint = n8n_data.get("hint", "")
            if mocked_hint:
                hint_source = "llm"

        except requests.exceptions.Timeout:
            logger.warning(
                "[AgenticCoach] n8n webhook TIMEOUT after 10s for user=%s problem=%s",
                user.id, problem_id
            )
        except requests.exceptions.ConnectionError as e:
            logger.error("[AgenticCoach] n8n CONNECTION ERROR for user=%s problem=%s: %s",
                         user.id, problem_id, e)
        except requests.exceptions.HTTPError as e:
            logger.error("[AgenticCoach] n8n HTTP ERROR %s for user=%s problem=%s: %s",
                         getattr(e.response, 'status_code', 'unknown'), user.id, problem_id, e)
        except (ValueError, KeyError) as e:
            # ValueError covers response.json() decode failures
            logger.error("[AgenticCoach] n8n returned malformed JSON for user=%s problem=%s: %s",
                         user.id, problem_id, e)
        except Exception as e:
            # Last-resort catch — still logged, never silent.
            logger.exception("[AgenticCoach] Unexpected error calling n8n for user=%s problem=%s: %s",
                              user.id, problem_id, e)

    if not mocked_hint:
        mocked_hint = _get_fallback_hint(failed_attempts)

    AgenticCoachLog.objects.create(
        user=user,
        question_id=problem_id,
        failed_attempts_count=failed_attempts,
        generated_hint=mocked_hint,
        hint_source=hint_source,
        webhook_latency_ms=webhook_latency_ms,
        webhook_fired=bool(webhook_url),
    )

    return mocked_hint
