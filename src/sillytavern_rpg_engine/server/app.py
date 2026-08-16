"""FastAPI app: OpenAI-compatible endpoints over the turn pipeline."""

import sqlite3
import tempfile
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from ..config import Settings
from ..domain.errors import (
    AmbiguousEntityError,
    BranchResolutionError,
    DomainError,
    NotFoundError,
    StaleStateError,
    ValidationError,
)
from ..llm.client import LLMClient, LLMError
from ..orchestration.graph import TurnRunner, default_services
from ..persistence.database import Database
from ..persistence.migrations import MigrationRunner
from ..services.campaign_export import CampaignExporter
from ..services.database_backup import DatabaseBackupService
from ..services.diagnostics import build_diagnostics

_STATUS = {
    ValidationError: (400, "invalid_request_error"),
    NotFoundError: (404, "not_found"),
    StaleStateError: (409, "stale_state"),
    AmbiguousEntityError: (409, "ambiguous_entity"),
    BranchResolutionError: (409, "branch_resolution_error"),
}


def _error_body(exc: Exception, type_: str) -> dict[str, Any]:
    return {"error": {"message": str(exc), "type": type_, "code": None}}


def _branch_diagnostics(database: Database, campaign_id: str) -> dict[str, Any]:
    """List each branch with its head pointer and the campaign-wide count of
    detached turns. Branches with no head row yet (fresh campaigns) report
    ``state_version`` 0 and a null ``latest_turn_id``."""
    with database.connect() as connection:
        if connection.execute(
            "SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone() is None:
            raise NotFoundError(f"campaign {campaign_id} not found")
        branches = connection.execute(
            "SELECT id, parent_branch_id, status FROM branches"
            " WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
        heads = {
            row["branch_id"]: row
            for row in connection.execute(
                "SELECT branch_id, state_version, latest_turn_id"
                " FROM branch_heads WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
        }
        detached = connection.execute(
            "SELECT COUNT(*) FROM turns"
            " WHERE campaign_id = ? AND status = 'detached'",
            (campaign_id,),
        ).fetchone()[0]
    return {
        "branches": [
            {
                "id": row["id"],
                "parent_branch_id": row["parent_branch_id"],
                "status": row["status"],
                "head": {
                    "state_version": head["state_version"] if head else 0,
                    "latest_turn_id": head["latest_turn_id"] if head else None,
                },
            }
            for row in branches
            for head in [heads.get(row["id"])]
        ],
        "detached_turns": detached,
    }


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
    async def chat_completions(request: Request):
        if runner is None:
            return JSONResponse(
                _error_body(
                    RuntimeError("engine is in read-only diagnostic mode"),
                    "degraded_mode",
                ),
                status_code=503,
            )
        try:
            payload = await request.json()
        except ValueError:
            raise ValidationError("request body must be valid JSON")
        return runner.run(payload)

    @app.get("/health")
    def health(deep: int = 0):
        models: dict[str, str] = {
            "narrator": "configured",
            "critic": "configured" if settings.critic is not None else "disabled",
        }
        try:
            with database.connect() as connection:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
            pending = MigrationRunner(database).status().pending
        except sqlite3.Error as exc:
            return JSONResponse(
                {
                    "status": "degraded",
                    "integrity": str(exc),
                    "schema_version": None,
                    "pending_migrations": [],
                    "models": models,
                },
                status_code=503,
            )
        if deep:
            models["narrator"] = "up" if narrator.ping() else "down"
            if critic is not None:
                models["critic"] = "up" if critic.ping() else "down"
        return {
            "status": "degraded" if (degraded or integrity != "ok") else "ok",
            "integrity": integrity,
            "schema_version": version,
            "pending_migrations": pending,
            "degraded": degraded or integrity != "ok",
            "models": models,
        }

    @app.get("/admin/diagnostics")
    def admin_diagnostics() -> dict[str, Any]:
        return build_diagnostics(database, degraded=degraded, settings=settings)

    @app.get("/admin/database/backup")
    def admin_database_backup() -> FileResponse:
        """Download a checkpointed SQLite copy of the database; the escape
        hatch when migrations failed and the engine runs degraded."""
        dest_dir = tempfile.mkdtemp(prefix="rpg-backup-")
        backup = DatabaseBackupService(database).backup(dest_dir)
        return FileResponse(
            backup, media_type="application/octet-stream", filename=backup.name
        )

    @app.get("/admin/campaigns")
    def admin_campaigns() -> dict[str, Any]:
        """List campaign metadata ordered by id."""
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, state_version, last_active_branch_id"
                " FROM campaigns ORDER BY id"
            ).fetchall()
        return {"campaigns": [dict(row) for row in rows]}

    @app.get("/admin/campaigns/{campaign_id}/export")
    def admin_export(campaign_id: str) -> dict[str, Any]:
        return exporter.build_payload(campaign_id)

    @app.get("/admin/campaigns/{campaign_id}/branches")
    def admin_branches(campaign_id: str) -> dict[str, Any]:
        return _branch_diagnostics(database, campaign_id)

    return app
