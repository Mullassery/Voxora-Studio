"""Durable inbound CRM synchronization with per-record error reporting."""

from __future__ import annotations

from typing import Any, Dict, List

from ..db import Database
from ..models import normalize_phone
from .base import CrmAdapter, CrmError


def sync_connection(
    db: Database,
    adapter: CrmAdapter,
    tenant_id: str,
    connection_id: str,
    limit: int = 100,
    max_pages: int = 20,
    preserve_consent: bool = True,
) -> Dict[str, Any]:
    imported = 0
    updated = 0
    failed = 0
    errors: List[Dict[str, Any]] = []
    contact_ids: List[str] = []
    offset = 0
    pages = 0
    while pages < max_pages:
        try:
            page = adapter.list_contacts(limit=limit, offset=offset)
        except CrmError as error:
            errors.append({"page": pages, "error": str(error), "status": error.status})
            break
        if not page.contacts:
            break
        for contact in page.contacts:
            try:
                phone = normalize_phone(contact.phone) if contact.phone else ""
                if not phone:
                    raise ValueError("contact has no usable phone number")
                values = contact.as_values()
                values["phone"] = phone
                existing = db.get_contact_by_external(
                    tenant_id, adapter.provider, contact.external_id
                )
                stored = db.upsert_contact(
                    tenant_id,
                    adapter.provider,
                    contact.external_id,
                    values,
                    preserve_consent=preserve_consent,
                )
                if existing:
                    updated += 1
                else:
                    imported += 1
                contact_ids.append(stored["id"])
            except Exception as error:  # noqa: BLE001 - per-record isolation is intentional
                failed += 1
                errors.append({"externalId": contact.external_id, "error": str(error)})
        offset += len(page.contacts)
        pages += 1
        if not page.has_next:
            break
    return {
        "contactIds": contact_ids[:1000],
        "imported": imported,
        "updated": updated,
        "failed": failed,
        "errors": errors[:100],
        "cursor": str(offset),
        "pages": pages,
    }
