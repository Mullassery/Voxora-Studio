"""Versioned HTTP API surface. All routes are tenant-scoped."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from . import __version__
from .campaign import CampaignEngine, CampaignError
from .config import Settings, TenantPrincipal
from .crm import build_crm_adapter, provider_catalog
from .crm.sync import sync_connection
from .db import Database, database_from_settings, iso
from .models import (
    AgentCreate,
    CampaignCreate,
    CampaignSyncRequest,
    ConsentUpdate,
    ContactCreate,
    ContactImport,
    CrmConnectionCreate,
    CrmSyncRequest,
    MappingsUpdate,
    RecipientAdd,
    SuppressionCreate,
)
from .observability import configure_logging
from .security import SecretBox, safe_equal
from .seed import seed_demo
from .state import InvalidTransition
from .telephony import build_telephony_adapter, telephony_provider_status
from .worker import CampaignWorker

logger = logging.getLogger(__name__)
SUPPORTED_CANONICAL_FIELDS = {
    "first_name",
    "last_name",
    "email",
    "phone",
    "timezone",
    "locale",
    "consent_state",
    "suppression",
    "call_activity",
}


class ApiError(Exception):
    def __init__(self, status_code: int, message: str, code: str = "api_error", details: Optional[Any] = None):
        self.status_code = status_code
        self.message = message
        self.code = code
        self.details = details
        super().__init__(message)


def _error_body(code: str, message: str, details: Any = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def _page(items: List[Dict[str, Any]], total: int, limit: int, offset: int) -> Dict[str, Any]:
    return {
        "data": items,
        "pageInfo": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "hasNextPage": offset + len(items) < total,
        },
    }


def now_iso() -> str:
    return iso()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    app.state.started_at = datetime.now(timezone.utc)
    app.state.db.initialize()
    if settings.seed_demo:
        tenants = list(dict.fromkeys(principal.tenant_id for principal in settings.api_tokens.values()))
        app.state.seed = [seed_demo(app.state.db, tenant_id) for tenant_id in tenants]
    recovered = app.state.db.recover_inflight()
    if recovered:
        logger.info("recovered incomplete work after restart", extra={"recovered": recovered})
    app.state.telephony = build_telephony_adapter(settings)
    app.state.engine = CampaignEngine(
        db=app.state.db,
        telephony=app.state.telephony,
        from_number=settings.twilio_from_number if settings.allow_live_calls else None,
        worker_threads=settings.worker_threads,
        global_concurrency=settings.global_concurrency,
    )
    app.state.worker = None
    if settings.worker_enabled:
        worker = CampaignWorker(app.state.engine, poll_seconds=settings.worker_poll_seconds)
        worker.start()
        app.state.worker = worker
    try:
        yield
    finally:
        worker = app.state.worker
        if worker:
            worker.stop()


def _principal_from_request(request: Request) -> TenantPrincipal:
    settings: Settings = request.app.state.settings
    authorization = request.headers.get("authorization") or ""
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    token = request.headers.get("x-api-key") or token
    if not token:
        raise HTTPException(status_code=401, detail=_error_body("unauthorized", "Missing API token"))
    for candidate, principal in settings.api_tokens.items():
        if safe_equal(token, candidate):
            request.state.principal = principal
            return principal
    raise HTTPException(status_code=401, detail=_error_body("unauthorized", "Invalid API token"))


def _require_writer(principal: TenantPrincipal) -> TenantPrincipal:
    if not principal.can_write():
        raise HTTPException(status_code=403, detail=_error_body("forbidden", "This action requires the operator role"))
    return principal


def _connection_view(connection: Dict[str, Any]) -> Dict[str, Any]:
    view = dict(connection)
    view.pop("api_key_encrypted", None)
    view.pop("webhook_secret_encrypted", None)
    view["hasApiKey"] = bool(connection.get("api_key_encrypted"))
    view["hasWebhookSecret"] = bool(connection.get("webhook_secret_encrypted"))
    return view


def create_app_from_env() -> "FastAPI":
    """Uvicorn --factory entry point that builds the app from environment."""
    from .config import Settings

    settings = Settings.from_env()
    if not settings.api_tokens:
        raise RuntimeError(
            "Refusing to start without API tokens; set VOXORA_API_TOKENS "
            "as a JSON mapping of token -> {tenant_id, role, name}."
        )
    configure_logging(settings.log_level)
    return create_app(settings)


def _urlencoded(raw: bytes) -> Dict[str, str]:
    import urllib.parse

    return {key: value for key, value in urllib.parse.parse_qsl(raw.decode("utf-8"), keep_blank_values=True)}


def _twiml_text(agent: Dict[str, Any], call: Dict[str, Any]) -> str:
    prompt = (agent or {}).get("system_prompt") or ""
    prompt = prompt.strip().replace("\n", " ")
    if not prompt:
        name = str((call or {}).get("id") or "").strip()
        prompt = f"Hello, this is a simulated Voxora outbound call ({name}). Goodbye."
    return prompt[:400]


def create_app(
    settings: Optional[Settings] = None,
    adapter_factory: Optional[Callable[[Dict[str, Any], SecretBox], Any]] = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Voxora Studio", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = database_from_settings(settings)
    app.state.secret_box = SecretBox(settings.secret_key)
    app.state.started_at = None
    app.state.seed = None
    app.state.worker = None
    default_factory: Callable[[Dict[str, Any], SecretBox], Any] = (
        adapter_factory or (lambda connection, secret_box: build_crm_adapter(connection, secret_box))
    )
    app.state.default_adapter_factory = default_factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-correlation-id"],
    )

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: Callable[[Request], Any]) -> Any:
        correlation = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        request.state.correlation_id = correlation
        response = await call_next(request)
        response.headers["x-correlation-id"] = correlation
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=_error_body(error.code, error.message, error.details))

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        detail = error.detail
        if isinstance(detail, dict) and "error" in detail:
            body = detail
        else:
            body = _error_body("http_error", str(detail))
        return JSONResponse(status_code=error.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
        failures = [
            {"path": ".".join(str(part) for part in item["loc"][1:]), "message": item["msg"]}
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error", "Request validation failed", failures),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled API error")
        return JSONResponse(status_code=500, content=_error_body("internal_error", "Internal server error"))

    # ---------------------------------------------------------------- health
    @app.get("/api/v1/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "mode": "simulation",
            "liveCallsEnabled": bool(app.state.settings.allow_live_calls),
            "time": now_iso(),
        }

    @app.get("/api/v1/ready")
    def ready() -> Dict[str, Any]:
        db_ok = False
        try:
            db_ok = app.state.db.ping()
        except Exception:
            logger.exception("readiness probe failed")
        worker = app.state.worker
        return {
            "ready": db_ok,
            "database": db_ok,
            "worker": {
                "enabled": bool(app.state.settings.worker_enabled),
                "running": bool(worker and worker.is_alive()),
            },
            "telephony": app.state.telephony.status() if hasattr(app.state, "telephony") else {"provider": "pending"},
            "time": now_iso(),
        }

    # ------------------------------------------------------------ overview
    @app.get("/api/v1/overview")
    def overview(principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        data = app.state.db.overview(principal.tenant_id)
        data["telephony"] = app.state.telephony.status()
        data["providers"] = telephony_provider_status(app.state.settings)
        data["mode"] = {"liveCallsEnabled": bool(app.state.settings.allow_live_calls), "simulated": True}
        return data

    # -------------------------------------------------------------- agents
    @app.post("/api/v1/agents", status_code=201)
    def create_agent(
        payload: AgentCreate, principal: TenantPrincipal = Depends(_principal_from_request)
    ) -> Dict[str, Any]:
        _require_writer(principal)
        agent = app.state.db.create_agent(principal.tenant_id, payload.model_dump())
        app.state.db.audit(principal.tenant_id, principal.name, "agent.created", "agent", agent["id"])
        return agent

    @app.get("/api/v1/agents")
    def list_agents(principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return {"data": app.state.db.list_agents(principal.tenant_id)}

    # ----------------------------------------------------------- campaigns
    @app.post("/api/v1/campaigns", status_code=201)
    def create_campaign(
        payload: CampaignCreate, principal: TenantPrincipal = Depends(_principal_from_request)
    ) -> Dict[str, Any]:
        _require_writer(principal)
        if not app.state.db.get_agent(principal.tenant_id, payload.agent_id):
            raise ApiError(404, "Agent not found", "agent_not_found")
        if payload.crm_connection_id:
            connection = app.state.db.get_connection(principal.tenant_id, payload.crm_connection_id)
            if not connection:
                raise ApiError(404, "CRM connection not found", "connection_not_found")
        schedule = payload.schedule_at.astimezone(timezone.utc).isoformat() if payload.schedule_at else None
        campaign = app.state.db.create_campaign(
            principal.tenant_id,
            {
                **payload.model_dump(),
                "crm_connection_id": payload.crm_connection_id,
                "schedule_at": schedule,
                "status": "draft",
            },
        )
        app.state.db.audit(
            principal.tenant_id,
            principal.name,
            "campaign.created",
            "campaign",
            campaign["id"],
            {"dryRun": payload.dry_run, "maxConcurrency": payload.max_concurrency},
        )
        return _campaign_view(app.state.db, campaign)

    @app.get("/api/v1/campaigns")
    def list_campaigns(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        status: Optional[str] = Query(default=None),
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        from .state import CAMPAIGN_STATUSES

        if status and status not in CAMPAIGN_STATUSES:
            raise ApiError(422, "Unknown campaign status", "invalid_status")
        items, total = app.state.db.list_campaigns(principal.tenant_id, limit, offset, status)
        return _page([_campaign_view(app.state.db, c) for c in items], total, limit, offset)

    @app.get("/api/v1/campaigns/{campaign_id}")
    def get_campaign(
        campaign_id: str, principal: TenantPrincipal = Depends(_principal_from_request)
    ) -> Dict[str, Any]:
        campaign = app.state.db.get_campaign(principal.tenant_id, campaign_id)
        if not campaign:
            raise ApiError(404, "Campaign not found", "campaign_not_found")
        return _campaign_view(app.state.db, campaign)

    def _transition(campaign_id: str, action: str, principal: TenantPrincipal, engine_method: str) -> Dict[str, Any]:
        _require_writer(principal)
        try:
            result = getattr(app.state.engine, engine_method)(principal.tenant_id, campaign_id)
        except KeyError:
            raise ApiError(404, "Campaign not found", "campaign_not_found") from None
        except (CampaignError, InvalidTransition) as error:
            raise ApiError(409, str(error), "invalid_transition") from None
        return result

    @app.post("/api/v1/campaigns/{campaign_id}/start")
    def start_campaign(campaign_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return _campaign_view(app.state.db, _transition(campaign_id, "start", principal, "start"))

    @app.post("/api/v1/campaigns/{campaign_id}/pause")
    def pause_campaign(campaign_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return _campaign_view(app.state.db, _transition(campaign_id, "pause", principal, "pause"))

    @app.post("/api/v1/campaigns/{campaign_id}/resume")
    def resume_campaign(campaign_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return _campaign_view(app.state.db, _transition(campaign_id, "resume", principal, "resume"))

    @app.post("/api/v1/campaigns/{campaign_id}/cancel")
    def cancel_campaign(campaign_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return _campaign_view(app.state.db, _transition(campaign_id, "cancel", principal, "cancel"))

    @app.post("/api/v1/campaigns/{campaign_id}/schedule")
    def schedule_campaign(campaign_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return _campaign_view(app.state.db, _transition(campaign_id, "schedule", principal, "schedule"))

    @app.get("/api/v1/campaigns/{campaign_id}/validate")
    def validate_campaign(
        campaign_id: str, principal: TenantPrincipal = Depends(_principal_from_request)
    ) -> Dict[str, Any]:
        try:
            return app.state.engine.validate_recipients(principal.tenant_id, campaign_id)
        except KeyError:
            raise ApiError(404, "Campaign not found", "campaign_not_found") from None

    @app.post("/api/v1/campaigns/{campaign_id}/recipients", status_code=201)
    def add_recipients(
        campaign_id: str,
        payload: RecipientAdd,
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        _require_writer(principal)
        campaign = app.state.db.get_campaign(principal.tenant_id, campaign_id)
        if not campaign:
            raise ApiError(404, "Campaign not found", "campaign_not_found")
        counts = app.state.db.campaign_counts(principal.tenant_id, campaign_id)
        if counts.get("recipients", 0) + len(payload.contact_ids) > app.state.settings.max_campaign_recipients:
            raise ApiError(422, "Campaign recipient limit exceeded", "recipient_limit")
        added = app.state.db.add_recipients(principal.tenant_id, campaign_id, payload.contact_ids)
        app.state.db.audit(principal.tenant_id, principal.name, "campaign.recipients_added", "campaign", campaign_id, {"added": added})
        return {"added": added}

    @app.post("/api/v1/campaigns/{campaign_id}/recipients/import", status_code=201)
    def import_recipients(
        campaign_id: str,
        payload: ContactImport,
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        _require_writer(principal)
        campaign = app.state.db.get_campaign(principal.tenant_id, campaign_id)
        if not campaign:
            raise ApiError(404, "Campaign not found", "campaign_not_found")
        contact_ids: List[str] = []
        for contact in payload.contacts:
            stored = app.state.db.upsert_contact(
                principal.tenant_id,
                "local",
                str(uuid.uuid4()),
                contact.model_dump(),
                preserve_consent=False,
            )
            contact_ids.append(stored["id"])
        added = app.state.db.add_recipients(principal.tenant_id, campaign_id, contact_ids)
        app.state.db.audit(principal.tenant_id, principal.name, "campaign.contacts_imported", "campaign", campaign_id, {"imported": len(contact_ids), "added": added})
        return {"imported": len(contact_ids), "added": added}

    @app.post("/api/v1/campaigns/{campaign_id}/recipients/sync")
    def sync_campaign_recipients(
        campaign_id: str,
        payload: CampaignSyncRequest,
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        _require_writer(principal)
        campaign = app.state.db.get_campaign(principal.tenant_id, campaign_id)
        if not campaign:
            raise ApiError(404, "Campaign not found", "campaign_not_found")
        connection_id = payload.connection_id or campaign.get("crm_connection_id")
        if not connection_id:
            raise ApiError(422, "Campaign has no CRM connection", "no_crm_connection")
        result = _run_sync(jobless=True, tenant_id=principal.tenant_id, connection_id=connection_id, limit=payload.limit, max_pages=payload.max_pages, principal=principal)
        added = app.state.db.add_recipients(principal.tenant_id, campaign_id, result["contactIds"])
        app.state.db.audit(principal.tenant_id, principal.name, "campaign.recipients_synced", "campaign", campaign_id, {"imported": result["imported"], "updated": result["updated"], "added": added})
        return {**result, "addedToCampaign": added}

    # ------------------------------------------------------------- contacts
    @app.get("/api/v1/contacts")
    def list_contacts(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        search: Optional[str] = Query(default=None, max_length=120),
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        items, total = app.state.db.list_contacts(principal.tenant_id, limit, offset, search)
        return _page(items, total, limit, offset)

    @app.post("/api/v1/contacts", status_code=201)
    def create_contact(
        payload: ContactCreate, principal: TenantPrincipal = Depends(_principal_from_request)
    ) -> Dict[str, Any]:
        _require_writer(principal)
        contact = app.state.db.create_local_contact(principal.tenant_id, payload.model_dump())
        app.state.db.audit(principal.tenant_id, principal.name, "contact.created", "contact", contact["id"])
        return contact

    @app.get("/api/v1/contacts/{contact_id}")
    def get_contact(contact_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        contact = app.state.db.get_contact(principal.tenant_id, contact_id)
        if not contact:
            raise ApiError(404, "Contact not found", "contact_not_found")
        return contact

    @app.patch("/api/v1/contacts/{contact_id}/consent")
    def update_consent(
        contact_id: str,
        payload: ConsentUpdate,
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        _require_writer(principal)
        contact = app.state.db.update_contact_consent(principal.tenant_id, contact_id, payload.state, payload.source)
        if not contact:
            raise ApiError(404, "Contact not found", "contact_not_found")
        app.state.db.audit(principal.tenant_id, principal.name, "contact.consent_updated", "contact", contact_id, {"state": payload.state, "source": payload.source})
        return contact

    # ---------------------------------------------------------- suppression
    @app.get("/api/v1/suppression")
    def list_suppression(principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return {"data": app.state.db.list_suppression(principal.tenant_id)}

    @app.post("/api/v1/suppression", status_code=201)
    def add_suppression(
        payload: SuppressionCreate, principal: TenantPrincipal = Depends(_principal_from_request)
    ) -> Dict[str, Any]:
        _require_writer(principal)
        row = app.state.db.add_suppression(principal.tenant_id, payload.phone, payload.reason)
        app.state.db.audit(principal.tenant_id, principal.name, "suppression.added", "suppression", row["id"], {"phone": payload.phone[:6] + "***"})
        return row

    @app.delete("/api/v1/suppression/{phone}")
    def delete_suppression(phone: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        _require_writer(principal)
        from .models import normalize_phone

        if not app.state.db.delete_suppression(principal.tenant_id, normalize_phone(phone)):
            raise ApiError(404, "Suppression entry not found", "suppression_not_found")
        app.state.db.audit(principal.tenant_id, principal.name, "suppression.removed", "suppression", None, {"phone": phone[:6] + "***"})
        return {"deleted": True}

    # ---------------------------------------------------------------- calls
    @app.get("/api/v1/calls")
    def list_calls(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        campaign_id: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None, alias="status"),
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        from .state import CALL_STATUSES

        if status and status not in CALL_STATUSES:
            raise ApiError(422, "Unknown call status", "invalid_status")
        items, total = app.state.db.list_calls(principal.tenant_id, limit, offset, campaign_id, status)
        return _page(items, total, limit, offset)

    @app.get("/api/v1/calls/{call_id}")
    def get_call(call_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        call = app.state.db.get_call_detail(principal.tenant_id, call_id)
        if not call:
            raise ApiError(404, "Call not found", "call_not_found")
        return call

    @app.post("/api/v1/calls/{call_id}/cancel")
    def cancel_call(call_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        _require_writer(principal)
        call = app.state.db.get_call(principal.tenant_id, call_id)
        if not call:
            raise ApiError(404, "Call not found", "call_not_found")
        try:
            app.state.db.update_call(principal.tenant_id, call_id, target="canceled", ended_at=now_iso())
        except InvalidTransition as error:
            raise ApiError(409, str(error), "invalid_transition") from None
        app.state.db.add_call_event(principal.tenant_id, call_id, "canceled", now_iso(), {"actor": principal.name})
        app.state.db.audit(principal.tenant_id, principal.name, "call.canceled", "call", call_id)
        return app.state.db.get_call_detail(principal.tenant_id, call_id)

    # -------------------------------------------------------- telephony info
    @app.get("/api/v1/telephony/providers")
    def telephony_providers(principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return {
            "data": telephony_provider_status(app.state.settings),
            "active": telephony_provider_status(app.state.settings)[0]["id"] if app.state.settings.telephony_provider == "mock" else None,
        }

    # ---------------------------------------------------------- CRM adapters
    @app.get("/api/v1/integrations/crm/providers")
    def crm_providers(principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return {"data": provider_catalog()}

    @app.post("/api/v1/integrations/crm/connections", status_code=201)
    def create_connection(
        payload: CrmConnectionCreate, principal: TenantPrincipal = Depends(_principal_from_request)
    ) -> Dict[str, Any]:
        _require_writer(principal)
        secret_box: SecretBox = app.state.secret_box
        connection = app.state.db.create_connection(
            principal.tenant_id,
            payload.provider,
            payload.name,
            payload.base_url,
            secret_box.encrypt(payload.api_key),
            secret_box.encrypt(payload.webhook_secret) if payload.webhook_secret else None,
            {},
        )
        app.state.db.audit(principal.tenant_id, principal.name, "crm.connection_created", "connection", connection["id"], {"provider": payload.provider})
        return _connection_view(connection)

    @app.get("/api/v1/integrations/crm/connections")
    def list_connections(principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return {"data": [_connection_view(c) for c in app.state.db.list_connections(principal.tenant_id)]}

    @app.get("/api/v1/integrations/crm/connections/{connection_id}")
    def get_connection(connection_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        connection = app.state.db.get_connection(principal.tenant_id, connection_id)
        if not connection:
            raise ApiError(404, "CRM connection not found", "connection_not_found")
        return _connection_view(connection)

    @app.post("/api/v1/integrations/crm/connections/{connection_id}/test")
    def test_connection(connection_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        _require_writer(principal)
        connection = app.state.db.get_connection(principal.tenant_id, connection_id)
        if not connection:
            raise ApiError(404, "CRM connection not found", "connection_not_found")
        adapter = app._adapter_for(connection)  # type: ignore[attr-defined]
        try:
            test = adapter.test_connection()
        except Exception as error:  # noqa: BLE001 - test must not raise 500
            test = type(adapter.test_connection())(False, str(error), {})
        status_value = "connected" if test.ok else "error"
        capabilities = adapter.capabilities().as_dict()
        app.state.db.update_connection_test(
            principal.tenant_id,
            connection_id,
            status_value,
            capabilities=capabilities,
            error=None if test.ok else test.message,
        )
        app.state.db.audit(principal.tenant_id, principal.name, "crm.connection_tested", "connection", connection_id, {"ok": test.ok})
        return {"ok": test.ok, "message": test.message, "details": {k: v for k, v in test.details.items() if k != "capabilities"}, "capabilities": capabilities}

    @app.get("/api/v1/integrations/crm/connections/{connection_id}/fields")
    def describe_fields(connection_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        connection = app.state.db.get_connection(principal.tenant_id, connection_id)
        if not connection:
            raise ApiError(404, "CRM connection not found", "connection_not_found")
        adapter = app._adapter_for(connection)  # type: ignore[attr-defined]
        fields = [
            {"canonicalField": f.canonical_field, "externalField": f.external_field, "supported": f.supported, "required": f.required, "note": f.note}
            for f in adapter.describe_fields()
        ]
        return {"data": fields, "capabilities": adapter.capabilities().as_dict()}

    @app.put("/api/v1/integrations/crm/connections/{connection_id}/mappings")
    def put_mappings(
        connection_id: str,
        payload: MappingsUpdate,
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        _require_writer(principal)
        connection = app.state.db.get_connection(principal.tenant_id, connection_id)
        if not connection:
            raise ApiError(404, "CRM connection not found", "connection_not_found")
        adapter = app._adapter_for(connection)  # type: ignore[attr-defined]
        available = {item.canonical_field: item for item in adapter.describe_fields()}
        for mapping in payload.mappings:
            info = available.get(mapping.canonical_field)
            if info is None:
                raise ApiError(422, f"Unknown canonical field: {mapping.canonical_field}", "unknown_field")
            if info.supported and mapping.external_field != info.external_field:
                raise ApiError(422, f"External field {mapping.external_field} does not match {mapping.canonical_field}", "invalid_mapping")
            if not info.supported:
                raise ApiError(422, f"{mapping.canonical_field} is not supported by this provider", "unsupported_field")
        mappings = app.state.db.replace_mappings(principal.tenant_id, connection_id, [m.model_dump() for m in payload.mappings])
        app.state.db.audit(principal.tenant_id, principal.name, "crm.mappings_updated", "connection", connection_id, {"count": len(mappings)})
        return {"data": mappings}

    @app.get("/api/v1/integrations/crm/connections/{connection_id}/mappings")
    def get_mappings(connection_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        connection = app.state.db.get_connection(principal.tenant_id, connection_id)
        if not connection:
            raise ApiError(404, "CRM connection not found", "connection_not_found")
        return {"data": app.state.db.get_mappings(principal.tenant_id, connection_id)}

    def _run_sync(jobless: bool, tenant_id: str, connection_id: str, limit: int, max_pages: int, principal: TenantPrincipal) -> Dict[str, Any]:
        connection = app.state.db.get_connection(tenant_id, connection_id)
        if not connection:
            raise ApiError(404, "CRM connection not found", "connection_not_found")
        adapter = app._adapter_for(connection)  # type: ignore[attr-defined]
        mappings = app.state.db.get_mappings(tenant_id, connection_id)
        adapter_with_mappings = app.state.default_adapter_factory(
            connection, app.state.secret_box
        ) if mappings else adapter
        job = app.state.db.create_sync_job(tenant_id, connection_id)
        app.state.db.update_sync_job(tenant_id, job["id"], status="running", started_at=now_iso())
        try:
            result = sync_connection(
                app.state.db,
                adapter_with_mappings,
                tenant_id,
                connection_id,
                limit,
                max_pages,
            )
            app.state.db.update_sync_job(
                tenant_id,
                job["id"],
                status="completed",
                imported=result["imported"],
                updated=result["updated"],
                failed=result["failed"],
                errors=result["errors"],
                cursor=result["cursor"],
                completed_at=now_iso(),
            )
        except Exception as error:  # noqa: BLE001 - job record must capture the failure
            app.state.db.update_sync_job(tenant_id, job["id"], status="failed", errors=[{"error": str(error)}], completed_at=now_iso())
            raise ApiError(502, f"Squid sync failed: {error}", "sync_failed") from None
        return result

    @app.post("/api/v1/integrations/crm/connections/{connection_id}/sync")
    def sync_connection_endpoint(
        connection_id: str,
        payload: CrmSyncRequest,
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        _require_writer(principal)
        result = _run_sync(True, principal.tenant_id, connection_id, payload.limit, payload.max_pages, principal)
        app.state.db.audit(principal.tenant_id, principal.name, "crm.sync", "connection", connection_id, {"imported": result["imported"], "updated": result["updated"], "failed": result["failed"]})
        return result

    @app.get("/api/v1/integrations/crm/sync-jobs")
    def list_sync_jobs(principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return {"data": app.state.db.list_sync_jobs(principal.tenant_id)}

    @app.get("/api/v1/integrations/crm/sync-jobs/{job_id}")
    def get_sync_job(job_id: str, principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        job = app.state.db.get_sync_job(principal.tenant_id, job_id)
        if not job:
            raise ApiError(404, "Sync job not found", "sync_job_not_found")
        return job

    # -------------------------------------------------------------- webhooks
    @app.post("/api/v1/webhooks/squid/{connection_id}")
    async def squid_webhook(connection_id: str, request: Request) -> JSONResponse:
        from .crm.webhooks import process_squid_event

        connection = app.state.db.get_connection_unscoped(connection_id)
        if not connection:
            raise HTTPException(status_code=404, detail=_error_body("connection_not_found", "Connection not found"))
        raw_body = await request.body()
        if len(raw_body) > 2_000_000:
            raise HTTPException(status_code=413, detail=_error_body("payload_too_large", "Webhook body exceeds 2 MiB"))
        webhook_secret_encrypted = connection.get("webhook_secret_encrypted")
        webhook_secret = app.state.secret_box.decrypt(webhook_secret_encrypted) if webhook_secret_encrypted else None
        if not webhook_secret:
            raise HTTPException(status_code=400, detail=_error_body("webhook_not_configured", "Connection has no webhook secret"))
        adapter = app.state.default_adapter_factory(connection, app.state.secret_box)
        verified = adapter.verify_webhook(
            raw_body,
            dict(request.headers),
            webhook_secret,
        )
        if not verified.ok:
            raise HTTPException(status_code=401, detail=_error_body("invalid_signature", verified.reason or "Invalid webhook signature"))
        is_new = app.state.db.record_webhook_event(
            connection["tenant_id"],
            connection_id,
            str(verified.event_id),
            str(verified.event_type),
            verified.payload or {},
        )
        if not is_new:
            return JSONResponse(status_code=200, content={"status": "duplicate"})
        try:
            outcome = process_squid_event(
                app.state.db,
                adapter,
                connection["tenant_id"],
                connection_id,
                str(verified.event_type),
                verified.payload or {},
            )
            app.state.db.mark_webhook_processed(
                connection["tenant_id"], connection_id, str(verified.event_id), "processed", None
            )
        except Exception as error:  # noqa: BLE001 - keep webhook endpoint honest
            app.state.db.mark_webhook_processed(
                connection["tenant_id"], connection_id, str(verified.event_id), "failed", str(error)
            )
            logger.exception("webhook processing failed")
            outcome = {"status": "failed", "reason": str(error)}
        return JSONResponse(status_code=200, content=outcome)

    # ------------------------------------------------------------ twilio webhooks
    # Twilio calls these endpoints server-to-server: no API token, no tenant
    # principal (the callback carries none). Both verify X-Twilio-Signature
    # with the configured auth token; a missing/unconfigured token is an
    # honest 503, not a silent pass.

    @app.post("/api/v1/telephony/twilio/status")
    async def twilio_status_callback(request: Request) -> Response:
        settings = request.app.state.settings
        auth_token = settings.twilio_auth_token
        if settings.telephony_provider != "twilio" or not auth_token:
            raise HTTPException(status_code=503, detail=_error_body("twilio_not_configured", "Twilio callbacks require TWILIO_AUTH_TOKEN"))
        form = _urlencoded(await request.body())
        from .security import verify_twilio_signature

        if not verify_twilio_signature(auth_token, str(request.url), form, request.headers.get("x-twilio-signature")):
            raise HTTPException(status_code=403, detail=_error_body("invalid_signature", "Twilio signature verification failed"))
        call_sid = form.get("CallSid") or ""
        if not call_sid:
            raise HTTPException(status_code=400, detail=_error_body("missing_call_sid", "CallSid is required"))
        call = request.app.state.db.get_call_by_provider_call_id(call_sid)
        if call is None:
            raise HTTPException(status_code=404, detail=_error_body("call_not_found", "No call matches CallSid"))
        from datetime import datetime, timezone

        adapter = request.app.state.telephony
        try:
            result = adapter.callback_result(form, received_at=datetime.now(timezone.utc).isoformat())
        except Exception as error:  # noqa: BLE001 - malformed provider data is data
            raise HTTPException(status_code=400, detail=_error_body("invalid_callback", str(error))) from error
        outcome = request.app.state.engine.settle_provider_call(call_sid, result)
        if outcome == "unknown_call":
            raise HTTPException(status_code=404, detail=_error_body("call_not_found", "No call matches CallSid"))
        return Response(content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response/>", media_type="application/xml")

    @app.post("/api/v1/telephony/twilio/twiml/{call_id}")
    async def twilio_twiml(call_id: str, request: Request) -> Response:
        settings = request.app.state.settings
        auth_token = settings.twilio_auth_token
        if settings.telephony_provider != "twilio" or not auth_token:
            raise HTTPException(status_code=503, detail=_error_body("twilio_not_configured", "Twilio TwiML requires TWILIO_AUTH_TOKEN"))
        form = _urlencoded(await request.body())
        from .security import verify_twilio_signature

        if not verify_twilio_signature(auth_token, str(request.url), form, request.headers.get("x-twilio-signature")):
            raise HTTPException(status_code=403, detail=_error_body("invalid_signature", "Twilio signature verification failed"))
        db = request.app.state.db
        call = db.get_call_by_provider_call_id(call_id)
        if call is None:
            raise HTTPException(status_code=404, detail=_error_body("call_not_found", "No call matches CallSid"))
        campaign = db.get_campaign(call["tenant_id"], call["campaign_id"])
        agent = db.get_agent(call["tenant_id"], (campaign or {}).get("agent_id", "")) if campaign else None
        text = _twiml_text(agent or {}, call)
        escaped = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Say voice=\"Alice\">"
            + escaped
            + "</Say><Hangup/></Response>"
        )
        return Response(content=xml, media_type="application/xml")

    # ----------------------------------------------------------------- audit
    @app.get("/api/v1/audit")
    def list_audit(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        principal: TenantPrincipal = Depends(_principal_from_request),
    ) -> Dict[str, Any]:
        data = app.state.db.list_audit(principal.tenant_id, limit, offset)
        return {"data": data}

    def _adapter_for(connection: Dict[str, Any]) -> Any:
        return default_adapter_factory(connection, secret_box)

    def _campaign_view(db: Database, campaign: Dict[str, Any]) -> Dict[str, Any]:
        counts = db.campaign_counts(campaign["tenant_id"], campaign["id"])
        view = dict(campaign)
        view["dryRun"] = bool(campaign.get("dry_run"))
        view["recipients"] = counts
        return view

    # expose helpers for internal/test use
    def default_adapter_factory(connection: Dict[str, Any], secret_box: SecretBox) -> Any:
        return app.state.default_adapter_factory(connection, secret_box)

    secret_box = app.state.secret_box

    @app.get("/api/v1/system/info")
    def system_info(principal: TenantPrincipal = Depends(_principal_from_request)) -> Dict[str, Any]:
        return {
            "version": __version__,
            "startedAt": app.state.started_at.isoformat() if app.state.started_at else None,
            "seeded": app.state.seed,
            "settings": app.state.settings.public_summary(),
        }

    app._adapter_for = lambda connection: app.state.default_adapter_factory(connection, app.state.secret_box)  # type: ignore[attr-defined]
    return app
