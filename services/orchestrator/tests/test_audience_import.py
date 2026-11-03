"""Contract tests for the Squid CRM -> Voxora audience-import endpoint.

The endpoint under contract is `POST /api/v1/campaigns/import-audience`; the
shapes here mirror the ecosystem ADR (docs/migration/migration-adr.md ADR-3A).
Covers permitted-contact import, consent fail-closed behavior, duplicate
external ids, suppression, phone validation and provenance persistence.
"""

from __future__ import annotations

import typing

from fastapi.testclient import TestClient

if typing.TYPE_CHECKING:  # pragma: no cover
    from typing import Any, Dict


HEADERS = {"Authorization": "Bearer admin-token"}


def _seed(client: "TestClient") -> str:
    response = client.post(
        "/api/v1/agents",
        json={"name": "Audience agent", "system_prompt": "You call politely."},
        headers=HEADERS,
    )
    return response.json()["id"]


def _import_payload(agent_id: str, **overrides: "Any") -> "Dict[str, Any]":
    payload: "Dict[str, Any]" = {
        "source": "squid-crm",
        "audience_id": "aud_12345",
        "audience_version": "v3",
        "campaign_name": "Interview Screening November",
        "agent_id": agent_id,
        "contacts": [
            {
                "external_contact_id": "contact_001",
                "name": "Candidate One",
                "phone": "+15551234567",
                "metadata": {"candidate_id": "cand_001", "job_id": "job_001"},
                "consent": {"voice_contact_allowed": True, "recording_allowed": True},
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_import_creates_campaign_with_provenance_and_preserves_squid_ids(
    client: "TestClient",
) -> None:
    agent_id = _seed(client)
    response = client.post("/api/v1/campaigns/import-audience", headers=HEADERS, json=_import_payload(agent_id))
    assert response.status_code == 201
    body = response.json()
    assert body["added"] == 1 and body["imported"] == 1 and body["rejected"] == []
    campaign = client.get(f"/api/v1/campaigns/{body['campaign_id']}", headers=HEADERS).json()
    assert campaign["audience_source"] == "squid-crm"
    assert campaign["audience_id"] == "aud_12345"
    assert campaign["audience_version"] == "v3"
    contact = client.get("/api/v1/contacts", headers=HEADERS).json()["data"][0]
    assert contact["external_id"] == "contact_001"
    assert contact["custom_fields"]["audience_id"] == "aud_12345"


def test_reimport_is_idempotent_no_duplicate_members(client: "TestClient") -> None:
    agent_id = _seed(client)
    first = client.post("/api/v1/campaigns/import-audience", headers=HEADERS, json=_import_payload(agent_id))
    second = client.post("/api/v1/campaigns/import-audience", headers=HEADERS, json=_import_payload(agent_id))
    assert first.status_code == second.status_code == 201
    # Same external id + consent nationwide persists: upsert keeps the contact single.
    contacts = client.get("/api/v1/contacts", headers=HEADERS).json()["data"]
    matching = [c for c in contacts if c["external_id"] == "contact_001"]
    assert len(matching) == 1
    consent = client.get("/api/v1/contacts", headers=HEADERS).json()["data"][0]["consent_state"]
    assert consent == "granted"


def test_missing_consent_is_recorded_unknown_and_not_silently_widened(
    client: "TestClient",
) -> None:
    agent_id = _seed(client)
    payload = _import_payload(
        agent_id,
        contacts=[
            {
                "external_contact_id": "contact_002",
                "name": "Silent Consent",
                "phone": "+15551234568",
            }
        ],
    )
    response = client.post("/api/v1/campaigns/import-audience", headers=HEADERS, json=payload)
    assert response.status_code == 201 and response.json()["imported"] >= 1
    contact = client.get("/api/v1/contacts", headers=HEADERS).json()["data"][0]
    assert contact["consent_state"] == "unknown"
    # The engine must still refuse consent-unknown, never the import widening it.


def test_voice_consent_refusal_marks_suppressed_and_reports_reasons(
    client: "TestClient",
) -> None:
    agent_id = _seed(client)
    payload = _import_payload(
        agent_id,
        contacts=[
            {
                "external_contact_id": "contact_003",
                "phone": "+15551234569",
                "consent": {"voice_contact_allowed": False},
            },
            {"external_contact_id": "contact_001", "phone": "+15551234567"},
        ],
    )
    response = client.post("/api/v1/campaigns/import-audience", headers=HEADERS, json=payload)
    body = response.json()
    assert body["imported"] == 2
    contacts = client.get("/api/v1/contacts", headers=HEADERS).json()["data"]
    match_suppressed = [c for c in contacts if c["external_id"] == "contact_003"]
    assert match_suppressed[0]["suppressed"] in (True, 1)
    assert match_suppressed[0]["consent_state"] == "denied"
    assert match_suppressed[0]["suppression_reason"] == "audience_voice_consent_denied"


def test_invalid_phone_and_garbage_are_rejected_with_validation_errors(
    client: "TestClient",
) -> None:
    agent_id = _seed(client)
    payload = _import_payload(
        agent_id,
        contacts=[{"external_contact_id": "contact_004", "phone": "not-a-phone"}],
    )
    response = client.post("/api/v1/campaigns/import-audience", headers=HEADERS, json=payload)
    assert response.status_code == 422
    details = str(response.json())
    assert "E.164" in details


def test_global_suppression_blocks_contact_with_recorded_rejection(
    client: "TestClient",
) -> None:
    agent_id = _seed(client)
    blocked_number = "+15559990001"
    client.post("/api/v1/suppression", headers=HEADERS, json={"phone": blocked_number, "reason": "do-not-call"})
    payload = _import_payload(
        agent_id,
        contacts=[
            {"external_contact_id": "contact_005", "phone": blocked_number, "consent": {"voice_contact_allowed": True}}
        ],
    )
    body = client.post("/api/v1/campaigns/import-audience", headers=HEADERS, json=payload).json()
    assert body["imported"] == 0 and body["added"] == 0
    assert body["rejected"] == [{"external_contact_id": "contact_005", "reason": "suppressed_phone"}]
