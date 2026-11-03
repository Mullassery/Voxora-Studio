"""Voxora AI runtime: deterministic mock agent backend.

Honest scope: this package implements the *contract* the orchestrator will
use for agent reasoning (one deterministic reply method, explicit
capabilities) and a fully working mock implementation. No real LLM client is
implemented; ``capabilities`` lists what is actually implemented.
"""

from .engine import AgentBrain, AgentReply, AgentTurnError, MockAgent
from .openrouter import OpenRouterAgent, OpenRouterError, create_agent_from_env

__all__ = [
    "AgentBrain",
    "AgentReply",
    "AgentTurnError",
    "MockAgent",
    "OpenRouterAgent",
    "OpenRouterError",
    "__version__",
    "create_agent_from_env",
]

__version__ = "0.1.0"
