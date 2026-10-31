"""Inbound Squid webhook processing with idempotent upserts."""

from __future__ import annotations

from typing import Any, Dict

from ..db import Database
from .squid import SquidCrmAdapter


def process_squid_event(
    db: Database,
    adapter: SquidCrmAdapter,
    tenant_id: str,
    connection_id: str,
    event_type: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    object_name, _, operation = event_type.partition(".")
    data = payload.get("data")
    if not isinstance(data, dict):
        return {"status": "ignored", "reason": "no_record_payload"}
    try:
        contact = adapter.map_record(data)
    except Exception as error:  # noqa: BLE001 - malformed provider payload is data
        return {"status": "failed", "reason": str(error)}
    if operation in {"deleted", "soft_deleted", "destroyed"}:
        db.revoke_contact_by_external(
            tenant_id, adapter.provider, contact.external_id, f"deleted in Squid ({event_type})"
        )
        return {"status": "revoked", "externalId": contact.external_id}
    values = contact.as_values()
    if not values.get("phone"):
        return {"status": "ignored", "reason": "missing_phone", "externalId": contact.external_id}
    from ..models import normalize_phone

    try:
        values["phone"] = normalize_phone(values["phone"])
    except ValueError as error:
        return {"status": "ignored", "reason": str(error), "externalId": contact.external_id}
    db.upsert_contact(
        tenant_id,
        adapter.provider,
        contact.external_id,
        values,
        preserve_consent=True,
    )
    return {
        "status": "upserted",
        "externalId": contact.external_id,
        "object": object_name,
        "operation": operation,
    }
