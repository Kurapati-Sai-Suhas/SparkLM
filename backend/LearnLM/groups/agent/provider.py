"""
The LLM-backed planner (M2 P2.14 §24A).

`Orchestrator` takes `planner(observation) -> dict`. This module supplies one
backed by a real provider. Nothing else about the loop changes: the planner
is still a plain callable, the tests still pass a scripted one, and the
guarantees the orchestrator makes hold whatever comes back from here.

── Which provider, and the defect found while checking (§24A) ──────────────

§24A said to verify the provider configuration before implementing. Doing so
found that the obvious thing to reuse is broken.

`ai_services._generate_json_with_fallback` hard-codes
`llama-3.3-70b-versatile`. **That model has been withdrawn.** Groq answers
404, and the NVIDIA NIM backup is only reached on a DAILY QUOTA error, so a
404 never triggers it — the function returns `None` on every call. Verified
live, not inferred:

    Groq -> 404 'The model `llama-3.3-70b-versatile` does not exist'
    _generate_json_with_fallback -> None

An agent built on it would have "worked" in the worst way: every request
falling back to the deterministic recommender, tests green, and no LLM ever
planning anything.

So this module reuses the pieces that DO work rather than the wrapper that
does not:

    model id      `reseed_generation.PROVIDER_MODELS["groq"]`, i.e. the
                  RESEED_GROQ_MODEL setting — verified by LISTING models,
                  not by spending a call to discover a 404
    exhaustion    `reseed_generation._is_exhausted`, which matches 404 and
                  429 on whole words
    backup        `ai_services._call_nim_raw`, unchanged

The defect in `_generate_json_with_fallback` is left alone deliberately: it
also serves `generate_full_question` and `generate_test_cases`, so fixing it
is a content-generation change and belongs to whoever owns that path. It is
already recorded in `docs/FEATURE_FLAGS.md`.

── Why not Gemini ──────────────────────────────────────────────────────────

Gemini serves embeddings and `AIService`, and its free tier is 20
requests/day. The agent runs on Groq with a NIM backup, so exhausting Gemini
does not disable the agent, and running the agent does not consume the
question-generation budget.

── Failure is a first-class outcome ────────────────────────────────────────

Every failure mode returns `{"stop": <reason>}` — a plan the orchestrator
answers deterministically from the backend. No exception escapes into the
request. A learner asking what to practise must never receive a stack trace
because a rate limit was hit.

`{"stop": ...}` rather than `{"final": None}`: the orchestrator stringifies
whatever `final` holds, so a null there would have handed the learner the
literal text "None". A stop has to be its own word.

No key is read, logged or handled here; `ai_services` holds them and this
module never sees a value.
"""

import json
import logging

from django.conf import settings

from groups.agent import tools as toolkit

logger = logging.getLogger(__name__)

def agent_enabled():
    """
    Off unless explicitly enabled.

    A missing flag means the deterministic recommender serves the learner,
    which is exactly the pre-agent behaviour — so shipping this code changes
    nothing until someone decides otherwise.
    """
    return bool(getattr(settings, "AGENT_ORCHESTRATOR_ENABLED", False))


#: Bumped whenever the prompt changes shape. Recorded on every response, so a
#: transcript can be traced to the instructions that produced it.
PROMPT_VERSION = "p2.14/v1"

SYSTEM_RULES = """\
You are SparkLM's practice planner. You choose which backend tool to call \
next, and nothing else.

Reply with ONE JSON object and no prose. Either:
  {"tool": "<name>", "arguments": {...}, "reasoning": "<one short line>"}
or:
  {"final": "<what to tell the learner>", "recommend": <question_id or null>, \
"reasoning": "<one short line>"}

Rules you cannot break:
- You may only name a tool from the list below.
- You may only name a question_id that a previous get_candidate_problems \
result actually returned. Inventing one is refused by the backend.
- When your answer recommends a specific problem, put its id in \
"recommend". The backend re-checks that id and will discard your whole \
answer if it was not offered, no longer exists, or is no longer verified. \
Use null when you are not recommending a specific problem.
- You cannot write to the database. Do not ask to.
- Stop as soon as you can answer. Fewer calls is better.

A good plan for "what should I practise next": read the learner's state, \
list candidates, check what the candidate needs, then give ONE \
recommendation naming the problem and why it suits them.

Tools:
%s
"""


def _tool_menu():
    lines = []
    for name, tool in toolkit.REGISTRY.items():
        arguments = []
        for argument in tool.required:
            arguments.append(f"{argument} (required)")
        for argument in tool.optional:
            arguments.append(f"{argument} (optional)")
        signature = ", ".join(arguments) or "no arguments"
        lines.append(f"- {name}({signature}): {tool.description}")
    return "\n".join(lines)


def _observation_text(observation):
    """
    The observation, trimmed to what a planner can act on.

    Results are truncated hard. A full problem statement or a learner's whole
    attempt history would push the prompt past the useful context and cost
    tokens to restate something the backend already holds — and the planner's
    job is to pick the next call, not to read the material.
    """
    parts = [f'Learner asked: "{observation.get("request", "")}"']
    results = observation.get("results") or []
    if not results:
        parts.append("No tool has been called yet.")
    else:
        parts.append("Results so far:")
        for entry in results:
            if "error" in entry:
                parts.append(f"- {entry.get('tool')}: ERROR {entry['error']}")
                continue
            body = json.dumps(entry.get("result"), default=str)
            if len(body) > 1200:
                body = body[:1200] + " ...(truncated)"
            parts.append(f"- {entry.get('tool')}: {body}")
    return "\n".join(parts)


def build_prompt(observation):
    return (SYSTEM_RULES % _tool_menu()) + "\n\n" + _observation_text(observation)


def _safe(exc):
    """
    An exception rendered for a log line, with any configured credential
    removed.

    Provider SDKs sometimes echo request details into an error message, and
    a traceback logged with `exc_info=True` carries whatever the upstream
    said. Logging the type plus a redacted, truncated message keeps the line
    useful without making the log a place credentials can appear.
    """
    message = f"{type(exc).__name__}: {exc}"[:300]
    for name in ("GROQ_API_KEY", "NIM_API_KEY", "GEMINI_API_KEY"):
        value = getattr(settings, name, None)
        if value and len(str(value)) > 8:
            message = message.replace(str(value), f"<{name} redacted>")
    return message


def complete_json(prompt, label):
    """
    One JSON completion. Returns a dict, or None if no provider could serve.

    Groq on the CONFIGURED model, then NVIDIA NIM when Groq reports a supply
    problem — 404 or 429, classified by `_is_exhausted` on whole words rather
    than by substring, because `"rate" in message` also matches "Failed to
    generate JSON".
    """
    from groups.ai_services import _call_nim_raw
    from groups.reseed_generation import PROVIDER_MODELS, _is_exhausted

    raw = None
    try:
        from django.conf import settings as django_settings
        from groq import Groq

        completion = Groq(api_key=django_settings.GROQ_API_KEY).chat.completions.create(
            model=PROVIDER_MODELS["groq"],
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2)
        raw = completion.choices[0].message.content
    except Exception as exc:                                  # noqa: BLE001
        if not _is_exhausted(exc):
            logger.error("%s: Groq call failed: %s", label, _safe(exc))
            return None
        logger.info("%s: Groq unavailable (%s) — trying NIM", label,
                    _safe(exc))
        raw = _call_nim_raw(prompt)
        if raw is None:
            return None

    body = (raw or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("%s: provider did not return JSON: %r", label, body[:200])
        return None
    return payload if isinstance(payload, dict) else None


def probe():
    """
    Is the agent's provider actually reachable? Lists models; generates
    nothing.

    Exists because §24A's check found a withdrawn model that only a real call
    would have revealed — and a call costs quota to learn what a listing
    tells you free.
    """
    from groups.reseed_generation import probe_provider

    return probe_provider("groq")


def llm_planner(generate=None):
    """
    A planner backed by the configured provider.

    `generate(prompt, label) -> dict | None` is injected so the tests can
    drive every failure path without a key or a network — the same seam
    `GradingService` uses for its runner, for the same reason.
    """
    if generate is None:
        generate = complete_json

    def plan(observation):
        try:
            payload = generate(build_prompt(observation), "agent_planner")
        except Exception as exc:                              # noqa: BLE001
            # Includes DailyQuotaExhausted. Every provider failure becomes a
            # deterministic stop, never an exception in a learner's request.
            #
            # No `exc_info=True`: a traceback carries whatever the upstream
            # said, and an SDK that echoes the request would put a credential
            # in the log.
            logger.warning("agent planner: provider unavailable — %s",
                           _safe(exc))
            return {"stop": "provider_unavailable"}

        if not isinstance(payload, dict):
            # `_generate_json_with_fallback` returns None for a failed call,
            # unparseable output, or a non-object payload. All three mean the
            # same thing here: no usable plan.
            logger.warning("agent planner: provider returned %s",
                           type(payload).__name__)
            return {"stop": "provider_no_plan"}
        return payload

    return plan
