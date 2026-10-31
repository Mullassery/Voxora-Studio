"""Voxora AI runtime: deterministic mock agent backend.

Honest scope: this package implements the *contract* the orchestrator will
use for agent reasoning (one deterministic reply method, explicit
capabilities) and a fully working mock implementation. No real LLM client is
implemented; ``capabilities`` lists what is actually implemented.
"""

from .engine import AgentReply, AgentTurnError, MockAgent

__all__ = ["AgentReply", "AgentTurnError", "MockAgent", "__version__"]

__version__ = "0.1.0"
