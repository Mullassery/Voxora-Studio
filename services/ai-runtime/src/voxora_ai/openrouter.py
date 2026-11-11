"""OpenRouter OpenAI-compatible language-model agent provider.

Ported from PyInterviewBot's verified implementation and adapted to Voxora's
synchronous agent contract (`start()` / `respond()` -> `AgentReply`).

Model selection is explicit (`OPENROUTER_MODEL`); API keys stay server-side
and are never logged or returned. OpenRouter bills per-token against the key
owner's OpenRouter account. To make that billing visible to the operator we
request `usage.include = true`; OpenRouter then reports `cost` (USD, as a
string) on the response, which we accumulate in `usage_report()`. No cost
reader is hidden or simulated — when the field is absent we record zeros and
say so.

Provider selection is explicit and fail-closed via `VOXORA_LLM_PROVIDER`
(`mock` default, `openrouter` opt-in); see `create_agent_from_env`.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

try:  # httpx is optional at import so `mock` keeps working without it
    import httpx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - httpx is a declared project dependency
    httpx = None

from .engine import AgentBrain, AgentReply, AgentTurnError, MockAgent

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_END_KEYWORDS = ("goodbye", "bye", "stop calling", "end call")

# Statuses worth retrying: timeout/conflict/rate-limit + upstream server errors.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


class OpenRouterError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _safe_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _strip_json(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_reply(content: str, turn_index: int) -> "AgentReply":
    """Parse model output as {"text": ..., "end_call": bool}.

    Non-JSON output raises so the caller can retry with a corrective
    instruction; the caller falls back to the raw text only after retries
    are exhausted.
    """
    data: Any
    if isinstance(content, dict):  # defensive
        data = content
    else:
        try:
            data = json.loads(_strip_json(str(content)))
        except (json.JSONDecodeError, TypeError) as error:
            raise ValueError(f"model returned non-JSON output: {str(content)[:80]!r}") from error
    if not isinstance(data, dict):
        raise ValueError("model JSON output is not an object")
    text = str(data.get("text") or data.get("message") or "").strip()
    ended = bool(data.get("end_call", data.get("is_complete", False)))
    return AgentReply(text=text or "(no reply)", ended=ended, turn_index=turn_index)


class OpenRouterAgent:
    """LLM agent over OpenRouter's OpenAI-compatible chat completions.

    Mirrors `MockAgent`'s surface: `start()` and `respond(user_text)` return
    the same `AgentReply` shape, and the same end-keywords close the call
    locally (belt and suspenders — the model is also allowed to set
    `"end_call": true`). Capabilities declare real_llm honestly.
    """

    provider = "openrouter"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_base_delay: float = 0.25,
        site_url: Optional[str] = None,
        app_name: Optional[str] = None,
        response_format: Optional[str] = None,
        client: Any = None,
        brain: Optional[AgentBrain] = None,
    ) -> None:
        if httpx is None:
            raise OpenRouterError("httpx is required for the openrouter provider")
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.model = model or os.environ.get("OPENROUTER_MODEL")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required for the openrouter provider")
        if not self.model:
            raise ValueError(
                "OPENROUTER_MODEL is required; set it to the exact OpenRouter model id"
            )
        self.base_url = (
            base_url or os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout if timeout is not None else float(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "60"))
        self.max_retries = max_retries if max_retries is not None else _env_int("OPENROUTER_MAX_RETRIES", 1)
        self.retry_base_delay = retry_base_delay
        self.system_prompt = system_prompt or os.environ.get(
            "OPENROUTER_SYSTEM_PROMPT",
            "You are the outbound AI voice agent for Voxora Studio. Reply with a short spoken line."
            ' Respond with JSON {"text": string, "end_call": boolean}.',
        )
        self._response_format = (
            response_format
            if response_format is not None
            else os.environ.get("OPENROUTER_RESPONSE_FORMAT", "json_object")
        )
        self._site_url = site_url or os.environ.get("OPENROUTER_SITE_URL")
        self._app_name = app_name or os.environ.get("OPENROUTER_APP_NAME")
        self._client = client or httpx.Client(base_url=self.base_url, timeout=self.timeout)
        self.brain = brain

        self.capabilities: Dict[str, bool] = {
            "real_llm": True,
            "audio_mixing": False,
            "barge_in_vad": False,
            "scripted": False,
        }
        self._started = False
        self._turn = 0
        self._history: List[Dict[str, str]] = []
        self._lock = threading.Lock()
        self.last_usage: Dict[str, float] = {}
        self._totals: Dict[str, float] = {"prompt_tokens": 0.0, "completion_tokens": 0.0, "total_tokens": 0.0, "cost_usd": 0.0}

    # ---------------------------------------------------------------- config
    def _headers(self) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._app_name:
            headers["X-Title"] = self._app_name
        return headers

    def usage_report(self) -> Dict[str, float]:
        """Accumulated billing data as reported by OpenRouter (`usage.include`)."""
        return dict(self._totals)

    # ------------------------------------------------------------------ flow
    def start(self) -> AgentReply:
        self._started = True
        self._turn = 0
        self._history = []
        return self._decide("The call just connected. Say your opening line.")

    def rearm(self) -> None:
        """Re-arm for a resumed live session without spending an LLM call.

        The opening line was already spoken (and is in the persisted
        transcript); resume only restores agent state so the next
        `respond()` continues the conversation.
        """
        self._started = True
        self._turn = 0
        self._history = []

    def respond(self, user_text: str) -> AgentReply:
        if not self._started:
            raise AgentTurnError("call has not been started; call start() first")
        lowered = str(user_text).strip().lower()
        self._turn += 1
        if any(keyword in lowered for keyword in _END_KEYWORDS):
            self._started = False
            return AgentReply("Understood. Ending the call now. Goodbye.", ended=True, turn_index=self._turn + 1)
        self._history.append({"role": "user", "content": str(user_text)})
        brain_turn = None
        if self.brain is not None:
            brain_turn = self.brain.respond(user_text, list(self._history))
        if brain_turn is not None and (brain_turn.text.strip() or brain_turn.ended):
            reply = brain_turn
        else:
            reply = self._decide(f"The contact just said: {user_text!r}")
        if reply.ended:
            self._started = False
        return reply

    # ------------------------------------------------------------------- LLM
    def _decide(self, user_prompt: str) -> AgentReply:
        messages = [{"role": "system", "content": self.system_prompt}, *self._history[-10:]]
        messages.append({"role": "user", "content": user_prompt})
        include_format = bool(self._response_format and self._response_format != "none")
        format_fallback_used = False
        last_error = "OpenRouter request failed"
        for attempt in range(self.max_retries + 1):
            payload: Dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.4,
                "usage": {"include": True},
            }
            if include_format:
                payload["response_format"] = {"type": self._response_format}
            try:
                response = self._client.post(
                    "/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
            except Exception as error:  # noqa: BLE001 - transport errors are retryable
                last_error = str(error)
                if attempt < self.max_retries:
                    import time

                    time.sleep(self.retry_base_delay * (2**attempt))
                    continue
                raise OpenRouterError(f"transport failure: {last_error}", retryable=True) from error

            status = response.status_code
            if status in {400, 422} and include_format and not format_fallback_used:
                include_format = False
                format_fallback_used = True
                continue
            if status >= 400:
                retryable = status in {408, 409, 429} or status >= 500
                detail = response.text[:300] if response.text else ""
                last_error = f"HTTP {status}: {detail}"
                if retryable and attempt < self.max_retries:
                    import time

                    time.sleep(self.retry_base_delay * (2**attempt))
                    continue
                raise OpenRouterError(last_error, status_code=status, retryable=retryable)

            try:
                body = response.json()
                message = body["choices"][0]["message"]["content"]
                usage = body.get("usage") or {}
                self._record_usage(usage)
                reply = _parse_reply(message if message is not None else "", self._turn + 1)
            except (KeyError, IndexError, TypeError, ValueError) as error:
                last_error = f"invalid model response: {error}"
                if attempt < self.max_retries:
                    messages = [*messages, {"role": "user", "content": "Return only the requested JSON object, with no markdown."}]
                    continue
                raw_text = (message if isinstance(message, str) else "").strip()
                if raw_text:
                    reply = AgentReply(text=raw_text, ended=False, turn_index=self._turn + 1)
                else:
                    raise OpenRouterError(last_error, retryable=False) from error
            self._history.append({"role": "assistant", "content": reply.text})
            return reply
        raise OpenRouterError(last_error, retryable=False)

    def _record_usage(self, usage: Dict[str, Any]) -> None:
        prompt = float(usage.get("prompt_tokens", 0) or 0)
        completion = float(usage.get("completion_tokens", 0) or 0)
        total = float(usage.get("total_tokens", prompt + completion) or prompt + completion)
        cost = usage.get("cost")
        cost_value = 0.0
        if cost is not None:
            try:
                cost_value = float(cost)  # OpenRouter reports cost as a USD string
            except (TypeError, ValueError):
                cost_value = 0.0
        self.last_usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "cost_usd": cost_value,
        }
        with self._lock:
            self._totals["prompt_tokens"] += prompt
            self._totals["completion_tokens"] += completion
            self._totals["total_tokens"] += total
            self._totals["cost_usd"] += cost_value

    # --------------------------------------------------------------- health
    def health(self) -> bool:
        """Reachable + authorized (OpenRouter key works for /models)."""
        try:
            response = self._client.get(
                "/models", headers=self._headers(), timeout=min(self.timeout, 10.0)
            )
        except Exception:  # noqa: BLE001 - network failure is unavailability
            return False
        return response.status_code < 400

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "capabilities": self.capabilities,
            "usage": self.usage_report(),
        }


def create_agent_from_env(env: Optional[Dict[str, str]] = None) -> Any:
    """Build the configured agent: VOXORA_LLM_PROVIDER = mock | openrouter."""
    source = env or os.environ
    selection = (source.get("VOXORA_LLM_PROVIDER") or "mock").strip().lower()
    if selection == "mock":
        return MockAgent()
    if selection == "openrouter":
        return OpenRouterAgent(
            api_key=source.get("OPENROUTER_API_KEY"),
            model=source.get("OPENROUTER_MODEL"),
            timeout=_safe_float(source.get("VOXORA_OPENROUTER_TIMEOUT")),
            system_prompt=source.get("OPENROUTER_SYSTEM_PROMPT"),
        )
    raise ValueError(
        f"Unknown VOXORA_LLM_PROVIDER '{selection}'; expected 'mock' or 'openrouter'"
    )


class OpenRouterLLM:
    """Stateless model-decision router (no conversation lifecycle).

    Used by the '/api/v1/ai/decide' endpoint: downstream applications keep
    their interview/methodology logic; Voxora hosts the key, chooses the
    model, and records usage honestly. Same retryable map and billing
    capture as OpenRouterAgent.
    """

    provider = "openrouter"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_base_delay: float = 0.25,
        site_url: Optional[str] = None,
        app_name: Optional[str] = None,
        client: Any = None,
    ) -> None:
        if httpx is None:
            raise OpenRouterError("httpx is required for the openrouter provider")
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.model = model or os.environ.get("OPENROUTER_MODEL")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required for the openrouter provider")
        if not self.model:
            raise ValueError("OPENROUTER_MODEL is required; set the exact OpenRouter model id")
        self.base_url = (
            base_url or os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout if timeout is not None else float(
            os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "60")
        )
        self.max_retries = max_retries if max_retries is not None else _env_int(
            "OPENROUTER_MAX_RETRIES", 1
        )
        self.retry_base_delay = retry_base_delay
        self._site_url = site_url or os.environ.get("OPENROUTER_SITE_URL")
        self._app_name = app_name or os.environ.get("OPENROUTER_APP_NAME", "Voxora Studio")
        # Synchronous client by default: Voxora's agent contract (`start()` /
        # `respond()` / `decide()`) is synchronous, so an AsyncClient here
        # would return unawaited coroutines and crash every call. Inject a
        # custom client for tests or exotic transports.
        self._client = client if client is not None else httpx.Client(
            base_url=self.base_url, timeout=self.timeout
        )
        self._tokens = {"prompt": 0, "completion": 0, "total": 0}
        self._cost_usd = 0.0
        self._lock = threading.Lock()

    def usage_report(self) -> Dict[str, float]:
        with self._lock:
            return {
                "prompt_tokens": float(self._tokens["prompt"]),
                "completion_tokens": float(self._tokens["completion"]),
                "total_tokens": float(self._tokens["total"]),
                "cost_usd": self._cost_usd,
            }

    def _record_usage(self, usage: Dict[str, Any]) -> None:
        with self._lock:
            self._tokens["prompt"] += int(usage.get("prompt_tokens") or 0)
            self._tokens["completion"] += int(usage.get("completion_tokens") or 0)
            self._tokens["total"] += int(usage.get("total_tokens") or 0)
            try:
                cost = usage.get("cost")
                self._cost_usd += float(cost) if cost is not None else 0.0
            except (TypeError, ValueError):
                pass

    def decide(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_format: str = "json_object",
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """One-shot decision: returns the parsed JSON object the model produced."""
        import time

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "usage": {"include": True},
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        headers: Dict[str, str] = {"Authorization": f"Bearer {self.api_key}"}
        if self._app_name:
            headers["X-Title"] = self._app_name
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        last_error = "OpenRouter request failed"
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post("/chat/completions", json=payload, headers=headers)
            except Exception as error:
                last_error = f"transport_failure: {type(error).__name__}: {error}"[:300]
                if attempt < self.max_retries:
                    time.sleep(self.retry_base_delay * (2**attempt))
                    continue
                raise OpenRouterError(last_error, retryable=True) from error
            if response.status_code in _RETRYABLE_STATUS:
                retry_after = response.headers.get("retry-after")
                try:
                    wait = float(retry_after) if retry_after else None
                except (TypeError, ValueError):
                    wait = None
                if attempt < self.max_retries:
                    time.sleep(wait or (self.retry_base_delay * (2**attempt)))
                    continue
                raise OpenRouterError(
                    f"openrouter_http_{response.status_code}", retryable=True
                )
            if response.status_code != 200:
                raise OpenRouterError(
                    f"openrouter_http_{response.status_code}",
                    retryable=response.status_code in _RETRYABLE_STATUS,
                )
            try:
                body = response.json()
                message = body["choices"][0]["message"]["content"]
                self._record_usage(body.get("usage") or {})
            except (KeyError, IndexError, TypeError) as error:
                raise OpenRouterError(
                    f"invalid model response: {error}", retryable=False
                ) from error
            stripped = _strip_json(message if isinstance(message, str) else "")
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise OpenRouterError(
                    f"model returned non-JSON output: {stripped[:80]!r}", retryable=False
                ) from error
            if not isinstance(data, dict):
                raise OpenRouterError("model JSON output is not an object", retryable=False)
            return data
        raise OpenRouterError(last_error, retryable=True)
