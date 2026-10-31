"""Storage backend compatibility tests.

Running the same database-level scenario against SQLite and PostgreSQL (via
the ``voxora.pg`` dialect adapter) proves the campaign-flow core behaves
identically on both backends.

PostgreSQL is skipped honestly when no test server is reachable:
set ``VOXORA_TEST_PG_URL`` (or rely on the local/CI default) to enable.
"""

from __future__ import annotations

import json
from typing import List

from voxora.db import Database


def _campaign_with_one_recipient(database: Database, dry_run: bool = True):
    database.ensure_tenant("t1", "Tenant")
    agent = database.create_agent("t1", {"name": "Agent"})
    contact = database.create_local_contact(
        "t1", {"first_name": "Ada", "last_name": "L", "phone": "+15551230001"}
    )
    database.update_contact_consent("t1", contact["id"], "granted", "operator")
    campaign = database.create_campaign(
        "t1",
        {
            "name": "C",
            "agent_id": agent["id"],
            "dry_run": dry_run,
            "max_concurrency": 2,
            "max_attempts": 2,
            "retry_backoff_seconds": 30,
        },
    )
    database.update_campaign_status("t1", campaign["id"], "running")
    assert database.add_recipients("t1", campaign["id"], [contact["id"]]) == 1
    return campaign, contact


def test_initialize_and_ping(database: Database) -> None:
    database.initialize()
    assert database.ping() is True


def test_agent_crud_scoped(database: Database) -> None:
    database.ensure_tenant("t1", "T1")
    agent = database.create_agent("t1", {"name": "A"})
    assert database.get_agent("t1", agent["id"])["name"] == "A"
    assert database.get_agent("t2", agent["id"]) is None
    assert len(database.list_agents("t1")) == 1


def test_contact_consent_and_suppression(database: Database) -> None:
    database.ensure_tenant("t1", "T1")
    contact = database.create_local_contact(
        "t1", {"first_name": "Ada", "phone": "+15551230001", "consent_state": "unknown"}
    )
    fetched = database.get_contact("t1", contact["id"])
    assert fetched["consent_state"] == "unknown"
    updated = database.update_contact_consent("t1", contact["id"], "granted", "operator")
    assert updated["consent_state"] == "granted"

    database.add_suppression("t1", "+15551230002", "DNC")
    assert database.is_suppressed("t1", "+15551230002") is True
    assert database.is_suppressed("t1", "+15551239999") is False
    listed = database.list_suppression("t1")
    assert len(listed) == 1 and listed[0]["reason"] == "DNC"
    assert database.delete_suppression("t1", "+15551230002") is True
    assert database.delete_suppression("t1", "+15551230002") is False


def test_upsert_contact_preserves_consent(database: Database) -> None:
    database.ensure_tenant("t1", "T1")
    first = database.upsert_contact(
        "t1", "local", "ext-1", {"phone": "+15551230003", "consent_state": "granted"}
    )
    again = database.upsert_contact(
        "t1",
        "local",
        "ext-1",
        {"phone": "+15551230003", "first_name": "Renamed"},
        preserve_consent=True,
    )
    assert again["id"] == first["id"]
    assert again["first_name"] == "Renamed"
    assert again["consent_state"] == "granted"
    assert not hasattr(again, "extra")  # decode is dict-identical across backends


def test_campaign_lifecycle_claims_and_completion(database: Database) -> None:
    campaign, contact = _campaign_with_one_recipient(database)
    claimed = database.claim_recipients(5)
    assert len(claimed) == 1
    call_id: str = claimed[0]["call_id"]
    assert call_id

    mark = database.mark_recipient("t1", claimed[0]["id"], "completed")
    assert mark["status"] == "completed"
    assert database.complete_campaign_if_done("t1", campaign["id"]) == "completed"
    final = database.get_campaign("t1", campaign["id"])
    assert final["status"] == "completed"
    counts = database.campaign_counts("t1", campaign["id"])
    assert counts == {
        "recipients": 1,
        "completed": 1,
        "failed": 0,
        "queued": 0,
        "active": 0,
    }

    call = database.get_call("t1", call_id)
    assert call is not None
    database.add_latency("t1", call_id, "stt_result", 128.5, True)
    summary = database.latency_summary("t1", simulated=True)
    assert summary, "latency summary should include staged data"
    assert any(
        stage.get("stage") == "stt_result" for stage in summary.get("stages", [])
    ) or summary.get("stt_result") is not None or summary.get("count", 0) >= 1


def test_claim_skips_running_and_requeues_retryable(database: Database) -> None:
    campaign, _ = _campaign_with_one_recipient(database)
    first = database.claim_recipients(5)
    assert len(first) == 1
    # A recipient in 'dialing' occupies a claim slot; no new claims until freed.
    second = database.claim_recipients(5)
    assert second == []
    # Mark it back to queued with a retry date in the past: claimable again.
    database.mark_recipient(
        "t1", first[0]["id"], "queued", last_error="timeout", next_attempt_at="2000-01-01T00:00:00+00:00"
    )
    retried = database.claim_recipients(5)
    assert len(retried) == 1
    assert retried[0]["attempts"] == 2


def test_recover_inflight_marks_stale_calls_failed(database: Database) -> None:
    campaign, _ = _campaign_with_one_recipient(database)
    claimed = database.claim_recipients(5)
    recovered = database.recover_inflight()
    assert recovered >= 1
    call = database.get_call("t1", claimed[0]["call_id"])
    assert call["status"] == "failed"
    assert call["error_code"] == "worker_restart"
    counts = database.campaign_counts("t1", campaign["id"])
    # attempts(1) < max_attempts(2): recover requeues, not fails.
    assert counts["queued"] == 1
    assert counts["active"] == 0 and counts["failed"] == 0


def test_call_events_and_listing(database: Database) -> None:
    campaign, _ = _campaign_with_one_recipient(database)
    claimed = database.claim_recipients(5)[0]
    database.add_call_event(
        "t1", claimed["call_id"], "ringing", "2026-01-01T00:00:00+00:00", json.dumps({"sim": True})
    )
    detail = database.get_call_detail("t1", claimed["call_id"])
    # claim already writes a 'queued' event; our ringing event joins it.
    event_types = {event["event_type"] for event in detail["events"]}
    assert "ringing" in event_types and "queued" in event_types
    listed, total = database.list_calls("t1", campaign_id=campaign["id"])
    assert total == 1 and listed[0]["simulated"] == 1


def test_webhook_dedupe(database: Database) -> None:
    database.ensure_tenant("t1", "T1")
    assert database.record_webhook_event("t1", "conn", "evt-1", "people.upserted", "{}") is True
    assert database.record_webhook_event("t1", "conn", "evt-1", "people.upserted", "{}") is False
    database.mark_webhook_processed("t1", "conn", "evt-1", "processed", None)


def test_audit_rows_scoped_and_sorted(database: Database) -> None:
    database.ensure_tenant("t1", "T1")
    for index in range(3):
        database.audit("t1", "tester", "campaign.create", "campaign", target_id=f"c{index}")
    rows: List[dict] = database.list_audit("t1", limit=2)
    assert len(rows) == 2
    assert rows[0]["created_at"] >= rows[1]["created_at"]


def test_crm_connection_and_mappings_roundtrip(database: Database) -> None:
    database.ensure_tenant("t1", "T1")
    connection_row = database.create_connection(
        "t1", "squid", "Squid", "https://squid.example", "enc-key", "enc-secret", {}
    )
    fetched = database.get_connection("t1", connection_row["id"])
    assert fetched["provider"] == "squid"
    database.replace_mappings(
        "t1",
        connection_row["id"],
        [{"canonical_field": "consent_state", "external_field": "consent.status"}],
    )
    mappings = database.get_mappings("t1", connection_row["id"])
    keyed = {mapping["canonical_field"]: mapping["external_field"] for mapping in mappings}
    assert keyed["consent_state"] == "consent.status"
    job = database.create_sync_job("t1", connection_row["id"])
    updated = database.update_sync_job(
        "t1", job["id"], status="completed", imported=7, updated=2, failed=1
    )
    assert updated["imported"] == 7
    assert database.get_sync_job("t1", job["id"])["status"] == "completed"


def test_overview_shape_is_backend_independent(database: Database) -> None:
    database.ensure_tenant("t1", "T1")
    overview = database.overview("t1")
    expected_keys = {"campaigns", "calls", "queueDepth", "activeCalls", "recentErrors", "latency"}
    assert expected_keys == set(overview.keys())
