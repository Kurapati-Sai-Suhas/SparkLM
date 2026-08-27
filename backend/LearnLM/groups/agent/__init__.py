"""
SparkLM agent (M2 P2.11a).

A thin orchestration layer over services that already exist. It decides
WHICH validated tool to call; it never computes trust, rating, mastery or
routing policy, and it never writes a row the orchestrator did not commit.
"""

from groups.agent.orchestrator import (MAX_TOOL_CALLS, TIMEOUT_SECONDS,
                                       AgentResult, Orchestrator)
from groups.agent.tools import (NARRATION, REGISTRY, Session, ToolDenied,
                                ToolError)

__all__ = ["Orchestrator", "AgentResult", "Session", "REGISTRY", "NARRATION",
           "ToolError", "ToolDenied", "MAX_TOOL_CALLS", "TIMEOUT_SECONDS"]
