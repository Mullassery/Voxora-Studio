"""Safe demo seed data. Clearly labeled simulated contacts and agents."""

from __future__ import annotations

from typing import Dict

from .db import Database, iso

DEMO_TENANT = "tenant-demo"

DEMO_CONTACTS = [
    {"first_name": "Ada", "last_name": "Lovelace", "phone": "+15551230001", "email": "ada@example.com", "timezone": "America/New_York", "consent_state": "granted"},
    {"first_name": "Grace", "last_name": "Hopper", "phone": "+15551230002", "email": "grace@example.com", "timezone": "America/New_York", "consent_state": "granted"},
    {"first_name": "Alan", "last_name": "Turing", "phone": "+15551230003", "email": "alan@example.com", "timezone": "Europe/London", "consent_state": "granted"},
    {"first_name": "Katherine", "last_name": "Johnson", "phone": "+15551230004", "email": "kjh@example.com", "timezone": "America/Chicago", "consent_state": "granted"},
    {"first_name": "Margaret", "last_name": "Hamilton", "phone": "+15551230005", "email": "mh@example.com", "timezone": "America/Los_Angeles", "consent_state": "granted"},
    {"first_name": "Linus", "last_name": "Torvalds", "phone": "+15551230006", "email": "lt@example.com", "timezone": "Europe/Helsinki", "consent_state": "granted"},
    {"first_name": "Guido", "last_name": "van Rossum", "phone": "+15551230007", "email": "gvr@example.com", "timezone": "America/Los_Angeles", "consent_state": "unknown"},
    {"first_name": "No", "last_name": "Consent", "phone": "+15551230008", "email": "nc@example.com", "timezone": "UTC", "consent_state": "revoked"},
]


def seed_demo(db: Database, tenant_id: str = DEMO_TENANT) -> Dict[str, object]:
    db.ensure_tenant(tenant_id, "Demo tenant")
    agents = db.list_agents(tenant_id)
    if not agents:
        db.create_agent(
            tenant_id,
            {
                "name": "Simulated outbound agent",
                "system_prompt": "Simulated agent. Always confirm the call is a demo.",
                "model": "mock-llm",
                "voice": "mock-voice",
                "stt_provider": "mock-stt",
                "tts_provider": "mock-tts",
            },
        )
    contacts, total = db.list_contacts(tenant_id, limit=1)
    if total == 0:
        for values in DEMO_CONTACTS:
            db.create_local_contact(tenant_id, values)
        db.add_suppression(tenant_id, "+15551230009", "Do-not-call (demo)")
    return {"tenant": tenant_id, "agents": len(db.list_agents(tenant_id)), "contacts": len(db.list_contacts(tenant_id, limit=1000)[0]), "seededAt": iso()}
