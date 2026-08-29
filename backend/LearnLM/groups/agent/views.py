"""
The agent endpoint (M2 P2.14 §24B, §24G).

    POST /api/ai/agent/          {"request": "What should I practise next?"}

Follows the conventions the rest of the API already uses: `APIView`,
`IsAuthenticated`, `ClientIPScopedRateThrottle` with a scope, and a flat JSON
response. It is deliberately the smallest thing that can invoke the
orchestrator.

── The learner always gets an answer ───────────────────────────────────────

Three layers, in order, and the request only descends when the layer above
cannot serve it:

    1. the agent, when enabled and the provider answers
    2. the orchestrator's own deterministic floor, when the loop gives up
    3. THIS view's fallback, when the agent is disabled or raises

Layer 3 is the pre-agent product: `NextProblemView`'s recommender, reached
through the same tools the agent uses so the two cannot drift. A learner who
asks what to practise gets a real recommendation whether or not an LLM is
reachable, whether or not a quota is exhausted, and whether or not the flag
is on.

── What never leaves this view ─────────────────────────────────────────────

`transcript` is the orchestrator's list of short human phrases. The model's
planning text lives in `_private` and is not read here. There is no code path
from a planner's reasoning to a response body, and a test asserts it.
"""

import logging

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.throttling import ClientIPScopedRateThrottle
from groups.agent import provider
from groups.agent import tools as toolkit
from groups.agent.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

#: Longest learner utterance accepted. Generous for a question, small enough
#: that the prompt cannot be stuffed with a payload.
MAX_REQUEST_CHARS = 500


class AgentRequestSerializer(serializers.Serializer):
    """What a learner may send. One field, bounded, required."""

    request = serializers.CharField(
        max_length=MAX_REQUEST_CHARS, allow_blank=False, trim_whitespace=True)
    topic = serializers.CharField(
        max_length=120, required=False, allow_blank=True)


class AgentRecommendView(APIView):
    """
    Ask the practice planner a question.

    POST body:   {"request": "...", "topic": "Arrays"}   topic optional
    Response:    {answer, transcript, tool_calls, source, stopped_because,
                  prompt_version}

    `source` names which of the three layers actually answered, so a caller
    can tell an agent recommendation from a deterministic one rather than
    guessing from the prose.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = 'agent'

    def post(self, request):
        payload = AgentRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        question = payload.validated_data["request"]

        session = toolkit.Session(user=request.user)

        if not provider.agent_enabled():
            return Response(self._deterministic(
                session, "agent_disabled"), status=status.HTTP_200_OK)

        try:
            result = Orchestrator(session, provider.llm_planner()).run(question)
        except Exception:                                     # noqa: BLE001
            # The orchestrator has its own floor; reaching here means
            # something outside the loop failed. The learner still gets a
            # recommendation.
            logger.exception("agent orchestrator raised for user=%s",
                             request.user.pk)
            return Response(self._deterministic(
                session, "orchestrator_error"), status=status.HTTP_200_OK)

        body = result.as_dict()
        body["source"] = ("agent" if result.stopped_because == "final"
                          else "agent_fallback")
        body["prompt_version"] = provider.PROMPT_VERSION
        return Response(body, status=status.HTTP_200_OK)

    # ── layer 3 ───────────────────────────────────────────────────────

    def _deterministic(self, session, reason):
        """
        The pre-agent answer, assembled from the same tools the agent uses.

        Reusing the tools rather than re-querying keeps one definition of
        "a question this learner may be served" — the trust filter included.
        """
        try:
            state = toolkit.get_learner_state(session)
            offered = toolkit.get_candidate_problems(session, limit=1)
        except Exception:                                     # noqa: BLE001
            logger.exception("deterministic fallback failed")
            return {
                "answer": "I could not fetch a recommendation just now. "
                          "Nothing was changed.",
                "transcript": [], "tool_calls": [],
                "source": "unavailable", "stopped_because": reason,
                "prompt_version": None,
            }

        candidate = (offered.get("candidates") or [None])[0]
        if candidate:
            answer = (f"Try {candidate['title']} next "
                      f"(difficulty {candidate['difficulty']}).")
        else:
            answer = ("There is no verified problem available to recommend "
                      "yet.")
        if state.get("elo_rating") is not None:
            answer += f" Your current rating is {state['elo_rating']}."

        return {
            "answer": answer,
            "transcript": ["Answering directly"],
            "tool_calls": [],
            "source": "deterministic",
            "stopped_because": reason,
            "prompt_version": None,
        }
