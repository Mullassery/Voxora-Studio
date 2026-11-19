"""Voxora AI runtime: deterministic mock agent backend and opt-in OpenRouter.

Honest scope: this package implements the *contract* the orchestrator uses for
agent reasoning (one deterministic reply method, explicit capabilities), a
fully working mock implementation, and an opt-in OpenRouter client. The mock
reports ``real_llm: False``; OpenRouter reports ``real_llm: True`` and is never
called by tests or the default demo.
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

__version__ = "0.2.0"
