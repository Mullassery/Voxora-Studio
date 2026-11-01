"""Replay harness: duplicate and out-of-order webhook delivery semantics.

Squid's delivery contract (verified against the client source): events carry a
unique ``x-squid-event-id`` and are signed with HMAC-SHA256 over the raw body.
This harness pins the semantics the orchestrator guarantees:

- replays of the same event id are no-ops that never re-apply data,
- a wrongly-signed (or tampered) replay is rejected before dedupe, and
- out-of-order deliveries resolve deterministically per external id because
  upserts match on (tenant, provider, external_id) — the last *accepted*
  delivery wins.

Postgres+SQLite note: these run on the API layer; storage differences are
covered by the compat suite.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Optional

AUTH = {"Authorization": "Bearer admin-token"}
WEBHOOK_SECRET = "whsec_replay_harness_0123456789"


def _secret() -> bytes:
    return WEBHOOK_SECRET.encode("utf-8")


def _sign(raw: bytes, secret: Optional[bytes] = None) -> str:
    return "sha256=" + hmac.new(secret if secret is not None else _secret(), raw, hashlib.sha256).hexdigest()


def _create_connection(client) -> str:
    created = client.post(
        "/api/v1/integrations/crm/connections",
        headers=AUTH,
        json={
            "provider": "squid",
            "name": "Replay",
            "base_url": "https://squid.example",
            "api_key": "api_replay_key",
            "webhook_secret": WEBHOOK_SECRET,
        },
    ).json()
    return created["id"]


def _person_payload(external_id: str, first: str, phone_number: str) -> dict:
    return {
        "data": {
            "id": external_id,
            "name": {"firstName": first, "lastName": "Replay"},
            "emails": {"primaryEmail": f"{external_id}@replay.example"},
            "phones": {
                "primaryPhoneNumber": phone_number[2:],
                "primaryPhoneCallingCode": phone_number[:2],
            },
        }
    }


def _delivery(payload: dict, event_id: str, event_type: str = "person.upserted"):
    raw = json.dumps(payload).encode("utf-8")
    return raw, {
        "Content-Type": "application/json",
        "x-squid-signature": _sign(raw),
        "x-squid-event": event_type,
        "x-squid-event-id": event_id,
        "x-squid-schema-version": "1",
        "x-squid-attempt": "1",
    }


def _post(client, connection_id: str, raw: bytes, headers: dict):
    return client.post(f"/api/v1/webhooks/squid/{connection_id}", content=raw, headers=headers)


def _contact_names(client) -> dict:
    data = client.get("/api/v1/contacts", headers=AUTH).json()["data"]
    return {contact["external_id"]: contact["first_name"] for contact in data if contact.get("external_id")}


def test_duplicate_delivery_of_same_event_is_noop(client) -> None:
    connection_id = _create_connection(client)
    raw, headers = _delivery(_person_payload("p-1", "Ada", "+15551230001"), "evt-42")

    first = _post(client, connection_id, raw, headers)
    second = _post(client, connection_id, raw, headers)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["status"] == "upserted"
    assert second.json()["status"] == "duplicate"
    assert _contact_names(client)["p-1"] == "Ada"


def test_replayed_upsert_does_not_resurrect_revoked_contact(client) -> None:
    connection_id = _create_connection(client)
    raw, headers = _delivery(_person_payload("p-del", "Ori", "+15551230002"), "evt-del")
    assert _post(client, connection_id, raw, headers).json()["status"] == "upserted"

    # Revoke via a deletion event (different event id, signed).
    deleted_payload = json.loads(_delivery(_person_payload("p-del", "Ori", "+15551230002"), "evt-del")[0])
    deleted_payload["data"] = {"id": "p-del"}
    del_raw = json.dumps(deleted_payload).encode("utf-8")
    del_headers = {
        "Content-Type": "application/json",
        "x-squid-signature": _sign(del_raw),
        "x-squid-event": "person.deleted",
        "x-squid-event-id": "evt-del-gone",
        "x-squid-schema-version": "1",
    }
    result = _post(client, connection_id, del_raw, del_headers)
    assert result.json()["status"] == "revoked"

    # The old delivery is replayed by the provider: dedupe blocks resurrection.
    replay = _post(client, connection_id, raw, headers)
    assert replay.json()["status"] == "duplicate"
    contacts = client.get("/api/v1/contacts", headers=AUTH).json()["data"]
    revoked = [contact for contact in contacts if contact.get("external_id") == "p-del"]
    assert revoked and revoked[0]["consent_state"] != "granted"


def test_tampered_replay_rejected_before_dedupe(client) -> None:
    connection_id = _create_connection(client)
    raw, headers = _delivery(_person_payload("p-tamper", "Eve", "+15551230003"), "evt-t")
    good = _post(client, connection_id, raw, headers)
    assert good.json()["status"] == "upserted"

    # Tamper: same event id, modified body, original signature.
    tampered = json.loads(raw)
    tampered["data"]["name"]["firstName"] = "Tampered"
    tampered_bytes = json.dumps(tampered).encode("utf-8")
    tampered_headers = dict(headers)
    response = _post(client, connection_id, tampered_bytes, tampered_headers)
    assert response.status_code == 401
    # The original record survives untouched.
    values = _contact_names(client)
    assert values.get("p-tamper") == "Eve"


def test_out_of_order_deliveries_last_accepted_delivery_wins(client) -> None:
    connection_id = _create_connection(client)
    older, older_headers = _delivery(_person_payload("p-ooo", "Older", "+15551230004"), "evt-ooo-1")
    newer, newer_headers = _delivery(_person_payload("p-ooo", "Newer", "+15551230004"), "evt-ooo-2")

    # Deliver newer first, then the older (arrived late / reordered).
    assert _post(client, connection_id, newer, newer_headers).json()["status"] == "upserted"
    assert _post(client, connection_id, older, older_headers).json()["status"] == "upserted"
    values = _contact_names(client)
    # Deterministic storage rule: last accepted delivery wins; documented
    # honestly here and in docs. Repeater replays are blocked by dedupe.
    assert values["p-ooo"] == "Older"
    # Re-delivering the newer event id is a duplicate and does NOT flip data.
    assert _post(client, connection_id, newer, newer_headers).json()["status"] == "duplicate"
    assert _contact_names(client)["p-ooo"] == "Older"
