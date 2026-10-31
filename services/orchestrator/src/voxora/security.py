"""Secret storage, token comparisons, and signed-webhook verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\s,\"'}]+"),
    re.compile(r"(?i)(token[\"']?\s*[:=]\s*[\"']?)[^\s,\"'}]+"),
]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def utc_token() -> str:
    return uuid.uuid4().hex


def safe_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def redact(value: Any) -> Any:
    """Redact obvious credentials from strings and nested structures."""
    if isinstance(value, str):
        result = value
        for pattern in _SECRET_PATTERNS:
            result = pattern.sub(r"\1[REDACTED]", result)
        return result
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if any(token in key.lower() for token in ("password", "secret", "token", "api_key")):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class SecretBox:
    """Reversible at-rest encryption for third-party credentials.

    The key is derived from VOXORA_SECRET_KEY. Ciphertext is versioned so a
    future key-rotation migration can re-wrap stored secrets without guessing.
    """

    def __init__(self, secret_key: str):
        digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        return "v1:" + self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith("v1:"):
            raise ValueError("Unsupported secret ciphertext version")
        try:
            return self._fernet.decrypt(ciphertext[3:].encode("ascii")).decode("utf-8")
        except InvalidToken as error:
            raise ValueError("Unable to decrypt stored secret; check VOXORA_SECRET_KEY") from error


def squid_signature(secret: str, raw_body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_squid_signature(secret: str, raw_body: bytes, header: Optional[str]) -> bool:
    if not header:
        return False
    return safe_equal(squid_signature(secret, raw_body), header)


@dataclass(frozen=True)
class VerifiedWebhook:
    ok: bool
    event_id: Optional[str]
    event_type: Optional[str]
    payload: Optional[Dict[str, Any]]
    reason: Optional[str] = None


def parse_squid_webhook(
    secret: str,
    raw_body: bytes,
    signature: Optional[str],
    event_id: Optional[str],
    event_type: Optional[str],
) -> VerifiedWebhook:
    if not verify_squid_signature(secret, raw_body, signature):
        return VerifiedWebhook(False, event_id, event_type, None, "invalid_signature")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return VerifiedWebhook(False, event_id, event_type, None, "invalid_json")
    if not isinstance(payload, dict):
        return VerifiedWebhook(False, event_id, event_type, None, "invalid_envelope")
    payload_event_id = str(payload.get("id") or event_id or "")
    payload_event_type = str(payload.get("event") or payload.get("type") or event_type or "")
    if not payload_event_id or not payload_event_type:
        return VerifiedWebhook(False, event_id, event_type, None, "missing_event_identity")
    return VerifiedWebhook(True, payload_event_id, payload_event_type, payload)
