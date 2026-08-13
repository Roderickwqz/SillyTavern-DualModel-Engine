"""FastAPI app: OpenAI-compatible endpoints over the turn pipeline."""

import sqlite3
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import Settings
from ..domain.errors import (
    AmbiguousEntityError,
    DomainError,
    NotFoundError,
    StaleStateError,
    ValidationError,
)
from ..llm.client import LLMClient, LLMError
from ..orchestration.graph import TurnRunner, default_services
from ..persistence.database import Database
from ..services.campaign_export import CampaignExporter

_STATUS = {
    ValidationError: (400, "invalid_request_error"),
    NotFoundError: (404, "not_found"),
    StaleStateError: (409, "stale_state"),
    AmbiguousEntityError: (409, "ambiguous_entity"),
    LLMError: (502, "upstream_error"),
}


def _error_body(exc: Exception, type_: str) -> dict[str, Any]:
    return {"error": {"message": str(exc), "type": type_, "code": None}}


def create_app(
    settings: Settings,
    database: Database,
    narrator: LLMClient,
    critic: LLMClient | None = None,
    *,
    degraded: bool = False,
) -> FastAPI:
    """Build the API around a wired turn runner; no module-level state.

    ``degraded=True`` is the read-only diagnostic mode for failed schema
    migrations: health and export answer, chat returns 503.
    """
    app = FastAPI(title="SillyTavern RPG Engine", version="0.1.0")
    runner = None if degraded else TurnRunner(
        default_services(database, settings, narrator, critic)
    )
    exporter = CampaignExporter(database)

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError):
        status, type_ = _STATUS.get(type(exc), (400, "invalid_request_error"))
        return JSONResponse(_error_body(exc, type_), status_code=status)

    @app.exception_handler(LLMError)
    async def _llm_error(request: Request, exc: LLMError):
        return JSONResponse(_error_body(exc, "upstream_error"), status_code=502)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        return JSONResponse(
            _error_body(RuntimeError("internal error"), "internal_error"),
            status_code=500,
        )

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        data = [{
            "id": settings.narrator.model, "object": "model",
            "created": 0, "owned_by": "rpg-engine",
        }]
        if settings.critic is not None:
            data.append({
                "id": settings.critic.model, "object": "model",
                "created": 0, "owned_by": "rpg-engine",
            })
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> dict[str, Any]:
        if runner is None:
            return JSONResponse(
                _error_body(
                    RuntimeError("engine is in read-only diagnostic mode"),
                    "degraded_mode",
                ),
                status_code=503,
            )
        return runner.run(await request.json())

    @app.get("/health")
    def health(deep: int = 0) -> dict[str, Any]:
        try:
            with database.connect() as connection:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
        except sqlite3.Error as exc:
            return JSONResponse(
                {"status": "degraded", "integrity": str(exc)}, status_code=503
            )
        models: dict[str, str] = {
            "narrator": "configured",
            "critic": "configured" if settings.critic is not None else "disabled",
        }
        if deep:
            models["narrator"] = "up" if narrator.ping() else "down"
            if critic is not None:
                models["critic"] = "up" if critic.ping() else "down"
        return {
            "status": "degraded" if (degraded or integrity != "ok") else "ok",
            "integrity": integrity,
            "schema_version": version,
            "models": models,
        }

    @app.get("/admin/campaigns/{campaign_id}/export")
    def admin_export(campaign_id: str) -> dict[str, Any]:
        return exporter.build_payload(campaign_id)

    return app
