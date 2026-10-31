"""CRM adapter registry. Provider-specific branches live only in this package."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..security import SecretBox
from .squid import SquidCrmAdapter


def provider_catalog() -> list:
    return [
        {
            "id": "squid",
            "name": "Squid CRM",
            "implemented": True,
            "verifiedAgainst": "Mullassery/Squid-CRM main @ 015a2e8",
            "capabilities": SquidCrmAdapter.capabilities().as_dict(),
            "notes": [
                "Connection tests use GET /rest/people?limit=1 because /rest/me rejects API keys.",
                "Squid has no consent, suppression, or call-activity object; those capabilities are disabled.",
                "Webhook signatures use x-squid-signature: sha256=<HMAC-SHA256(raw body)>.",
            ],
        }
    ]


def build_crm_adapter(
    connection: Dict[str, Any],
    secret_box: SecretBox,
    transport: Optional[Any] = None,
    mappings: Optional[list] = None,
):
    api_key = secret_box.decrypt(connection["api_key_encrypted"])
    provider = connection["provider"]
    if provider == "squid":
        return SquidCrmAdapter(
            base_url=connection["base_url"],
            api_key=api_key,
            transport=transport,
            mappings=mappings,
        )
    raise ValueError(f"Unsupported CRM provider: {provider}")
