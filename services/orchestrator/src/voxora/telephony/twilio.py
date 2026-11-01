"""Twilio Programmable Voice adapter (live-capable, honesty-gated).

Implemented against Twilio's *documented* REST contract:

- `POST /2010-04-01/AccountSid/Calls.json` (Basic auth `AccountSid:AuthToken`,
  form-encoded params: `To`, `From`, `Url` (TwiML), `StatusCallback`,
  `MachineDetection`).
- `POST Calls/{CallSid}.json` with `Status=canceled` to cancel.
- Responses are JSON via the `.json` resource suffix.
- `X-Twilio-Signature` (Base64 HMAC-SHA1 over URL + sorted params with the
  auth token) validates inbound callbacks — see `security.verify_twilio_signature`.

Honesty rules enforced here:
- `simulated` is False on real calls.
- Live dialing requires `VOXORA_ALLOW_LIVE_CALLS`; the adapter raises instead
  of silently refusing.
- `dry_run` requests stay provider-side: a synthetic completed result is
  returned and Twilio is never contacted.
- Live-server behavior is NOT verified in this slice (no credentials);
  contract fidelity is against Twilio's public API documentation.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Dict, Optional

from ..crm.http import HttpResponse, HttpTransport, UrllibTransport
from .base import CallRequest, TelephonyAdapter, TelephonyEvent, TelephonyResult

API_BASE = "https://api.twilio.com/2010-04-01"

_RETRYABLE = {429, 500, 502, 503, 504}

# Twilio CallStatus -> (call target state, disposition, retryable)
CALLBACK_STATUS_MAP: Dict[str, tuple] = {
    "completed": ("completed", "answered", False),
    "busy": ("busy", "busy", True),
    "no-answer": ("no_answer", "no_answer", True),
    "canceled": ("canceled", "canceled", False),
    "failed": ("failed", "carrier_failure", False),
}

# Twilio create-call status -> pending in-flight state
CREATE_STATUS_MAP = {"queued": None, "ringing": "ringing", "in-progress": "in_progress"}


class TwilioError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class TwilioTelephonyAdapter(TelephonyAdapter):
    provider = "twilio"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        twiml_url: str,
        from_number: str,
        live_enabled: bool = False,
        transport: Optional[HttpTransport] = None,
        status_callback: Optional[str] = None,
        machine_detection: Optional[str] = None,
        timeout: float = 20.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
    ):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.twiml_url = twiml_url
        self.from_number = from_number
        self.live_enabled = live_enabled
        self.transport = transport or UrllibTransport()
        self.status_callback = status_callback
        self.machine_detection = machine_detection
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self._results: Dict[str, TelephonyResult] = {}
        self.cancelled: set = set()

    @property
    def base_url(self) -> str:
        return f"{API_BASE}/Accounts/{self.account_sid}"

    # ------------------------------------------------------------------ dial
    def start_call(self, request: CallRequest) -> TelephonyResult:
        cached = self._results.get(request.idempotency_key)
        if cached is not None:
            return cached
        if request.dry_run:
            return self._dry_run_result(request)
        if not self.live_enabled:
            raise TwilioError(
                "Live Twilio dialing is disabled: set VOXORA_ALLOW_LIVE_CALLS=true "
                "(with real credentials) to enable outbound calls"
            )
        form: Dict[str, str] = {
            "To": request.to_number,
            "From": request.from_number or self.from_number,
            "Url": self.twiml_url,
        }
        status_callback = request.metadata.get("statusCallbackUrl") or self.status_callback
        if status_callback:
            form["StatusCallback"] = status_callback
            form["StatusCallbackEvent"] = "completed busy failed no-answer canceled"
        if self.machine_detection:
            form["MachineDetection"] = self.machine_detection
        response = self._request("POST", "Calls.json", form)
        body = response.json() if response.body else {}
        sid = str(body.get("sid") or "")
        if not sid:
            raise TwilioError("Twilio response is missing call sid", status=response.status)
        status = str(body.get("status") or "queued")
        result = TelephonyResult(
            ok=True,
            provider=self.provider,
            provider_call_id=sid,
            status="dialing",  # engine already moved the call to dialing
            disposition="dialed",
            simulated=False,
            retryable=False,
            final=False,
            duration_ms=0,
            events=[TelephonyEvent("dialing", _iso(), {"providerStatus": status, "callSid": sid})],
            started_at=_iso(),
        )
        self._results[request.idempotency_key] = result
        return result

    def _dry_run_result(self, request: CallRequest) -> TelephonyResult:
        name = (request.metadata.get("contactName") or "there").strip() or "there"
        transcript = [
            {"role": "assistant", "text": f"Hello {name}, this call would have been a live Twilio call."},
        ]
        result = TelephonyResult(
            ok=True,
            provider=self.provider,
            provider_call_id=None,
            status="completed",
            disposition="answered",
            simulated=True,  # dry-run: provider never contacted
            retryable=False,
            duration_ms=2_000,
            latency_ms={"turn_completed": 1020.0},
            transcript=transcript,
            started_at=_iso(),
            answered_at=_iso(),
            ended_at=_iso(),
        )
        self._results[request.idempotency_key] = result
        return result

    # ---------------------------------------------------------------- cancel
    def cancel_call(self, provider_call_id: str) -> bool:
        if provider_call_id in self.cancelled:
            return True
        try:
            self._request("POST", f"Calls/{provider_call_id}.json", {"Status": "canceled"})
        except TwilioError as error:
            # 404: call already terminal — treat as cancelled.
            if error.status == 404:
                self.cancelled.add(provider_call_id)
                return True
            return False
        self.cancelled.add(provider_call_id)
        return True

    # ---------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "available": True,
            "simulated": False,
            "liveCallsEnabled": self.live_enabled,
            "message": (
                "Live Twilio adapter implemented against the documented REST contract. "
                "Live-server behavior is not verified in this slice."
            )
            if self.live_enabled
            else "Twilio adapter ready; live dialing disabled (VOXORA_ALLOW_LIVE_CALLS).",
        }

    # -------------------------------------------------------------- callbacks
    def callback_result(self, form: Dict[str, str], received_at: Optional[str] = None) -> TelephonyResult:
        """Map a status-callback's form payload to a final TelephonyResult."""
        twilio_status = str(form.get("CallStatus", "")).strip().lower()
        mapped = CALLBACK_STATUS_MAP.get(twilio_status)
        if not mapped:
            raise TwilioError(f"Unknown Twilio CallStatus: {twilio_status!r}")
        target, disposition, retryable = mapped
        duration = None
        if form.get("CallDuration"):
            try:
                duration = int(form["CallDuration"]) * 1000
            except ValueError:
                duration = None
        return TelephonyResult(
            ok=target == "completed",
            provider=self.provider,
            provider_call_id=str(form.get("CallSid") or ""),
            status=target,
            disposition=disposition,
            simulated=False,
            retryable=retryable,
            final=True,
            duration_ms=duration or 0,
            error_code=None if target == "completed" else twilio_status,
            error_message=None if target == "completed" else f"Twilio call status: {twilio_status}",
            events=[
                TelephonyEvent(
                    twilio_status,
                    received_at or _iso(),
                    {"source": "twilio-status-callback"},
                )
            ],
            ended_at=received_at or _iso(),
        )

    # -------------------------------------------------------------------- IO
    def _request(
        self,
        method: str,
        path: str,
        form: Optional[Dict[str, str]] = None,
    ) -> HttpResponse:
        url = f"{self.base_url}/{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": _basic(self.account_sid, self.auth_token),
        }
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        attempts = 1 + self.max_retries
        last_error: Optional[TwilioError] = None
        for attempt in range(attempts):
            try:
                response = self.transport.request(
                    "GET" if form is None else "POST", url, headers, body, self.timeout
                )
            except ConnectionError as connection_error:
                last_error = TwilioError(str(connection_error), retryable=True)
                if attempt + 1 < attempts:
                    time.sleep(self._retry_delay(attempt, None))
                    continue
                raise last_error from connection_error
            if 200 <= response.status < 300:
                return response
            error = self._error(response)
            if error.retryable and attempt + 1 < attempts:
                time.sleep(self._retry_delay(attempt, response.headers.get("retry-after")))
                continue
            raise error
        raise last_error or TwilioError("Twilio request failed after retries", retryable=True)

    @staticmethod
    def _error(response: HttpResponse) -> TwilioError:
        try:
            body = response.json()
        except Exception:
            body = response.body.decode("utf-8", errors="replace")
        message = "Twilio request failed"
        if isinstance(body, dict):
            message = str(body.get("message") or body.get("code") or message)
        else:
            message = str(body)[:200]
        return TwilioError(
            message,
            status=response.status,
            retryable=response.status in _RETRYABLE,
        )

    def _retry_delay(self, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 30.0)
            except ValueError:
                pass
        return min(self.retry_backoff * (2**attempt), 5.0)


def _basic(user: str, secret: str) -> str:
    import base64

    return "Basic " + base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")


def _iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
