"""
The agent loop (M2 P2.11a).

    observe -> plan -> tool -> result -> next action -> final response

No LangChain, no LangGraph. The loop is thirty lines because that is all it
is; a framework here would add a dependency, a vocabulary and an upgrade
treadmill in exchange for indirection over `while`.

── What this class guarantees, whatever the model does ─────────────────────

    MAX_TOOL_CALLS      the loop always terminates
    TIMEOUT_SECONDS     wall-clock, checked before each call
    unknown tool        refused, counted, fed back as an error
    bad arguments       refused before the handler runs
    `commit`            STRIPPED from every model-supplied payload
    repeated failure    falls back deterministically

The fallback is the point. A model that stalls, loops or emits nonsense must
still leave the learner with something correct, so `_fallback` answers from
the backend alone with no model involved.

── The UI sees actions, never reasoning ────────────────────────────────────

`transcript` holds one short human phrase per step — "Checking learner
state". The model's own planning text is kept in `_private` and is never
returned by `run()`. Chain-of-thought is a debugging artifact, not a
product surface: it is frequently wrong even when the answer is right, and
showing it invites a learner to trust the wrong half.
"""

import json
import logging
import time

from groups.agent import tools as toolkit

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 8
TIMEOUT_SECONDS = 30.0
MAX_CONSECUTIVE_ERRORS = 3


class AgentResult:
    def __init__(self, answer, transcript, calls, stopped_because):
        self.answer = answer
        self.transcript = transcript
        self.calls = calls
        self.stopped_because = stopped_because

    def as_dict(self):
        return {"answer": self.answer, "transcript": self.transcript,
                "tool_calls": self.calls,
                "stopped_because": self.stopped_because}


class Orchestrator:
    """
    Drives one request to completion.

    `planner(observation) -> dict` is any callable returning either
    `{"tool": name, "arguments": {...}}` or `{"final": text}`. A live
    deployment passes an LLM-backed planner; the tests pass a scripted one.
    Keeping it a plain callable is what makes the loop testable without a
    provider, a key or a network.
    """

    def __init__(self, session, planner, *, max_tool_calls=MAX_TOOL_CALLS,
                 timeout_seconds=TIMEOUT_SECONDS, clock=time.monotonic):
        self.session = session
        self.planner = planner
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.transcript = []
        self.calls = []
        self._private = []

    # ── the loop ──────────────────────────────────────────────────────

    def run(self, request):
        started = self.clock()
        observation = {"request": request, "results": []}
        errors = 0

        for step in range(self.max_tool_calls):
            if self.clock() - started > self.timeout_seconds:
                return self._stop("timeout")

            try:
                plan = self.planner(observation)
            except Exception:                                 # noqa: BLE001
                logger.exception("planner raised")
                return self._stop("planner_error")

            if not isinstance(plan, dict):
                errors += 1
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    return self._stop("planner_unusable")
                observation["results"].append(
                    {"error": "plan must be an object"})
                continue

            self._private.append(plan.get("reasoning"))

            if "final" in plan:
                return AgentResult(str(plan["final"]), self.transcript,
                                   self.calls, "final")

            name = plan.get("tool")
            arguments = plan.get("arguments") or {}

            try:
                result = self._invoke(name, arguments)
                errors = 0
            except toolkit.ToolDenied as denied:
                # Never retried: a denial is a decision, not a hiccup.
                logger.warning("tool denied: %s", denied)
                self.calls.append({"tool": name, "outcome": "denied"})
                return self._stop("denied")
            except toolkit.ToolError as error:
                errors += 1
                self.calls.append({"tool": name, "outcome": "error"})
                observation["results"].append(
                    {"tool": name, "error": str(error)})
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    return self._stop("too_many_tool_errors")
                continue

            observation["results"].append({"tool": name, "result": result})

        return self._stop("max_tool_calls")

    # ── one call ──────────────────────────────────────────────────────

    def _invoke(self, name, arguments):
        tool = toolkit.REGISTRY.get(name)
        if tool is None:
            raise toolkit.ToolError(
                f"unknown tool {name!r}; available: "
                f"{sorted(toolkit.REGISTRY)}")

        # `commit` is the orchestrator's word, never the model's. Stripping
        # rather than rejecting keeps a confused model usable while making
        # the escalation it asked for impossible.
        if "commit" in arguments:
            logger.warning("planner attempted commit=%r on %s; stripped",
                           arguments.get("commit"), name)
            arguments = {k: v for k, v in arguments.items() if k != "commit"}

        checked = tool.validate(arguments)
        self.transcript.append(toolkit.NARRATION.get(name, "Working"))
        started = self.clock()
        result = tool.handler(self.session, **checked)
        self.calls.append({
            "tool": name, "outcome": "ok", "reads_only": tool.reads_only,
            "ms": round((self.clock() - started) * 1000, 1),
            "arguments": _loggable(checked),
        })
        return result

    # ── the deterministic floor ───────────────────────────────────────

    def _stop(self, reason):
        """
        A correct answer assembled from the backend, with no model involved.

        Reached whenever the loop gives up. It is deliberately dull: state,
        and one trusted question if one exists.
        """
        logger.info("agent fell back: %s", reason)
        self.transcript.append("Falling back to a direct answer")
        try:
            state = toolkit.get_learner_state(self.session)
            offered = toolkit.get_candidate_problems(self.session, limit=1)
        except Exception:                                     # noqa: BLE001
            logger.exception("fallback failed")
            return AgentResult(
                "I could not complete that just now. Nothing was changed.",
                self.transcript, self.calls, reason)

        candidate = (offered.get("candidates") or [None])[0]
        if candidate:
            answer = (f"Here is a problem to try next: "
                      f"{candidate['title']} (difficulty "
                      f"{candidate['difficulty']}).")
        else:
            answer = "There is no verified problem available to recommend yet."
        if state.get("elo_rating") is not None:
            answer += f" Your current rating is {state['elo_rating']}."
        return AgentResult(answer, self.transcript, self.calls, reason)


def _loggable(arguments):
    """Argument summary for the log: no source code, no statements."""
    out = {}
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > 60:
            out[key] = f"<{len(value)} chars>"
        else:
            out[key] = value
    try:
        json.dumps(out)
    except TypeError:
        return {"unserialisable": True}
    return out
