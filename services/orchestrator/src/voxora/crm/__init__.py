"""CRM adapter registry. Provider-specific branches live only in this package."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..security import SecretBox
from .leadconnector import LeadConnectorCrmAdapter
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
        },
        {
            "id": "leadconnector",
            "name": "LeadConnector (GoHighLevel)",
            "implemented": True,
            "verifiedAgainst": "marketplace.gohighlevel.com API docs, Version 2021-07-28 (retrieved 2026-11-12); contract-verified, live-unverified",
            "capabilities": LeadConnectorCrmAdapter.capabilities().as_dict(),
            "notes": [
                "Requires a sub-account location_id in the connection config (config-in-db).",
                "Call suppression is read from dndSettings.Call.status (active|permanent = do-not-call); no documented consent object, so consent_read is False.",
                "Contacts list uses the documented id cursor (startAfterId); the endpoint is deprecated in favor of contacts/search — adapter notes it honestly.",
                "Live-instance verification still open (needs a LeadConnector token).",
            ],
        },
    ]


def build_crm_adapter(
    connection: Dict[str, Any],
    secret_box: SecretBox,
    transport: Optional[Any] = None,
    mappings: Optional[list] = None,
):
    api_key = secret_box.decrypt(connection["api_key_encrypted"])
    provider = connection["provider"]
    config = connection.get("config") or {}
    if provider == "squid":
        return SquidCrmAdapter(
            base_url=connection["base_url"],
            api_key=api_key,
            transport=transport,
            mappings=mappings,
        )
    if provider == "leadconnector":
        return LeadConnectorCrmAdapter(
            base_url=connection["base_url"],
            api_token=api_key,
            location_id=str(config.get("location_id") or ""),
            transport=transport,
        )
    raise ValueError(f"Unsupported CRM provider: {provider}")
