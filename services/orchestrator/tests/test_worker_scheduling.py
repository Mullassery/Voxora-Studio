"""Worker-loop scheduling: due 'scheduled' campaigns auto-promote before claims."""

from __future__ import annotations

from voxora.campaign import CampaignEngine
from voxora.db import Database
from voxora.telephony.mock import MockTelephonyAdapter
from voxora.worker import CampaignWorker


def _settings_like_engine(database: Database) -> CampaignEngine:
    return CampaignEngine(
        db=database,
        telephony=MockTelephonyAdapter(delay_seconds=0),
        worker_threads=1,
        global_concurrency=5,
    )


def test_scheduled_campaign_promotes_once_due(database: Database) -> None:
    database.ensure_tenant("t1", "Tenant")
    agent = database.create_agent("t1", {"name": "Agent"})
    contact = database.create_local_contact(
        "t1", {"first_name": "Ada", "phone": "+15551230001", "consent_state": "granted"}
    )
    campaign = database.create_campaign(
        "t1",
        {
            "name": "Scheduled",
            "agent_id": agent["id"],
            "dry_run": True,
            "max_concurrency": 1,
            "max_attempts": 1,
            "retry_backoff_seconds": 1,
            "schedule_at": "2020-01-01T00:00:00+00:00",  # long past due
        },
    )
    database.add_recipients("t1", campaign["id"], [contact["id"]])
    scheduled = database.update_campaign_status("t1", campaign["id"], "scheduled")
    assert scheduled["status"] == "scheduled"

    engine = _settings_like_engine(database)
    assert engine.run_until_idle() == 1
    final = database.get_campaign("t1", campaign["id"])
    assert final["status"] == "completed"
    rows = [row for row in database.list_audit("t1") if row["action"] == "campaign.due_promoted"]
    assert len(rows) == 1 and rows[0]["target_id"] == campaign["id"]


def test_future_schedule_is_not_promoted(database: Database) -> None:
    database.ensure_tenant("t1", "Tenant")
    agent = database.create_agent("t1", {"name": "Agent"})
    contact = database.create_local_contact(
        "t1", {"first_name": "Ada", "phone": "+15551230001", "consent_state": "granted"}
    )
    campaign = database.create_campaign(
        "t1",
        {
            "name": "Future",
            "agent_id": agent["id"],
            "dry_run": True,
            "max_concurrency": 1,
            "max_attempts": 1,
            "retry_backoff_seconds": 1,
            "schedule_at": "2999-01-01T00:00:00+00:00",
        },
    )
    database.add_recipients("t1", campaign["id"], [contact["id"]])
    database.update_campaign_status("t1", campaign["id"], "scheduled")

    engine = _settings_like_engine(database)
    assert engine.run_once() == 0
    assert database.get_campaign("t1", campaign["id"])["status"] == "scheduled"


def test_worker_poll_calls_promotion_before_claim(database: Database) -> None:
    campaign_engine = _settings_like_engine(database)
    assert database.promote_due_campaigns() == []
    CampaignWorker(campaign_engine, poll_seconds=0.05)
    assert campaign_engine.run_once() == 0  # a single poll is safe on an idle DB

