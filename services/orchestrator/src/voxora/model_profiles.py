"""Model-profile dispatch (roadmap M7/P8).

`agent.model_profile` stops being a recorded-but-ignored annotation: the
`model_profiles` table (seeded config-in-db from `builtin_model_profiles`)
dispatches per task — the interview turn task and the summarize/analyze
task may route to different models. `/api/v1/ai/decide` honors the
profile and reports which (profile, task, model) served it; unknown
profiles are refused with 501 and the configured list, never silently
mapped onto the default.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .db import DEFAULT_MODEL_PROFILE, MODEL_PROFILE_TASKS, Database, builtin_model_profiles


class UnknownModelProfileError(Exception):
    """The requested (profile, task) pair has no configured model row."""

    def __init__(self, profile: Optional[str], task: str, configured: List[str]) -> None:
        self.profile = profile
        self.task = task
        self.configured = configured
        super().__init__(
            f"unknown model profile {profile!r} for task {task!r};"
            f" configured profiles: {', '.join(configured) if configured else '(none)'}"
        )


def seed_model_profiles(db: Database, tenant_id: str) -> None:
    """Seed the tenant's profile rows from `builtin_model_profiles()`.

    Idempotent (INSERT OR IGNORE); rows already present — including rows
    an operator changed — are never overwritten by re-seeding.
    """
    db.seed_model_profiles(tenant_id, builtin_model_profiles())


def _configured_profile_names(db: Database, tenant_id: str) -> List[str]:
    return sorted({row["profile"] for row in db.list_model_profiles(tenant_id)})


def resolve_model_profile(
    db: Database,
    tenant_id: str,
    profile: Optional[str],
    task: str,
) -> Dict[str, Any]:
    """Resolve (profile, task) -> model; seed-once when the tenant is fresh.

    `profile=None` means the default profile. An unknown task or profile
    raises `UnknownModelProfileError` carrying the configured profile
    list, so the API can answer 501 honestly instead of silently falling
    back to the default model.
    """
    if task not in MODEL_PROFILE_TASKS:
        raise UnknownModelProfileError(profile, task, [])
    if db.list_model_profiles(tenant_id):
        name = profile or DEFAULT_MODEL_PROFILE
    else:
        seed_model_profiles(db, tenant_id)
        name = profile or DEFAULT_MODEL_PROFILE
    row = db.get_model_profile(tenant_id, name, task)
    if row is None:
        raise UnknownModelProfileError(name, task, _configured_profile_names(db, tenant_id))
    return {"profile": name, "task": task, "model": row["model"], "provider": "builtin"}
