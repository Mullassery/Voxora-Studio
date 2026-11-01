"""Durable inbound CRM synchronization with per-record error reporting.

Consent/suppression enrichment is capability-driven: adapters that declare
``consent_read``/``suppression_read`` have those attributes pulled per record
during sync; adapters without the capability (e.g. Squid, which has no consent
or suppression objects — verified against the client source) are skipped, and
attempting to call the unsupported read raises ``NotSupportedError`` honestly.

Suppression is escalation-only here: provider-supplied suppression states mark
contacts suppressed, but a "clear" state never un-suppresses. Clearing an
operator-applied suppression is a deliberate API action, not a side effect of
sync.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..db import Database
from ..models import normalize_phone
from .base import CrmAdapter, CrmError, NotSupportedError

_CONSENT_STATES = {"granted", "revoked", "unknown"}
_SUPPRESSED_STATES = {"suppressed", "blocked", "do_not_call", "dnc"}


def sync_connection(
    db: Database,
    adapter: CrmAdapter,
    tenant_id: str,
    connection_id: str,
    limit: int = 100,
    max_pages: int = 20,
    preserve_consent: bool = True,
) -> Dict[str, Any]:
    capabilities = adapter.capabilities()
    consent_enabled = capabilities.consent_read
    suppression_enabled = capabilities.suppression_read
    imported = 0
    updated = 0
    failed = 0
    consent_applied = 0
    suppression_applied = 0
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
                if consent_enabled:
                    consent = adapter.get_consent(contact.external_id)
                    state = str(consent.get("state", "")).strip().lower()
                    if state not in _CONSENT_STATES:
                        raise ValueError(f"provider returned unknown consent state: {state!r}")
                    source = str(consent.get("source", "")) or "crm"
                    db.update_contact_consent(tenant_id, stored["id"], state, source)
                    consent_applied += 1
                if suppression_enabled:
                    suppression = adapter.get_suppression(contact.external_id)
                    state = str(suppression.get("state", "")).strip().lower()
                    if state in _SUPPRESSED_STATES:
                        reason = str(suppression.get("reason", "")) or f"suppressed in {adapter.provider}"
                        db.suppress_contact(tenant_id, stored["id"], reason)
                        suppression_applied += 1
            except (CrmError, NotSupportedError, ValueError, TypeError) as error:
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
        "consentApplied": consent_applied,
        "suppressionApplied": suppression_applied,
        "errors": errors[:100],
        "cursor": str(offset),
        "pages": pages,
    }
