"""Test-only FastAPI factory wiring the scripted LLM double.

Used by the Playwright backend API e2e spec and the
``RPG_ENGINE_TEST_MODE=scripted`` serve path. No live model credentials are
touched: narrator and recorder roles are served by :class:`ScriptedLLMClient`.
"""

import json
import os
from dataclasses import replace

from fastapi import FastAPI

from ..config import Settings, load_settings
from ..llm import ScriptedLLMClient
from ..persistence.database import Database
from ..persistence.migrations import MigrationRunner
from ..services.campaigns import CampaignService
from .app import create_app

TEST_MODE_ENV = "RPG_ENGINE_TEST_MODE"
TEST_MODE_VALUE = "scripted"
DEFAULT_CAMPAIGN_ID = "playwright-c1"

_SCRIPTED_MODEL = "scripted-narrator"
_SCRIPTED_BASE_URL = "http://127.0.0.1:9/v1"
_DEFAULT_RESPONSES = ["（测试叙述）", json.dumps({"operations": []})]


def is_scripted_mode() -> bool:
    """True when ``RPG_ENGINE_TEST_MODE=scripted`` is set in the environment."""
    return os.environ.get(TEST_MODE_ENV) == TEST_MODE_VALUE


def scripted_settings(database_path: str) -> Settings:
    """Settings from the environment with scripted narrator defaults.

    ``RPG_NARRATOR_*`` env vars are honored when present; missing ones fall
    back to a scripted model config so no API key is required. The database
    path is always overridden to ``database_path``.
    """
    env = dict(os.environ)
    env.setdefault("RPG_NARRATOR_MODEL", _SCRIPTED_MODEL)
    env.setdefault("RPG_NARRATOR_BASE_URL", _SCRIPTED_BASE_URL)
    env.setdefault("RPG_DATABASE", database_path)
    return replace(load_settings(env), database_path=database_path)


def _create_campaign(database: Database, campaign_id: str) -> None:
    ids = iter(f"evt-{i}" for i in range(1000))
    CampaignService(
        database,
        id_factory=ids.__next__,
        clock=lambda: "2026-08-13T00:00:00Z",
    ).create_campaign(campaign_id, "Playwright")


def create_scripted_app(
    database_path: str,
    responses: list[str] | None = None,
    *,
    degraded: bool = False,
) -> FastAPI:
    """Build a FastAPI app backed by :class:`ScriptedLLMClient`.

    ``responses`` is the complete queue handed to the scripted client: each
    chat completion consumes two responses — the narrator narration string
    followed by the recorder tracker JSON. Queue ``2 * n_chats`` entries (e.g.
    ``["叙述一", json.dumps({"operations": []})]`` for one chat). When ``None``
    the default narration plus empty tracker is used.

    The database is migrated and campaign ``playwright-c1`` is pre-created so
    the spec's chat roundtrip has a fresh, known campaign. With
    ``degraded=True`` (failed migrations) the read-only diagnostic app is
    returned instead.
    """
    responses = list(DEFAULT_RESPONSES if responses is None else responses)
    database = Database(database_path)
    if not degraded:
        MigrationRunner(database).apply()
        _create_campaign(database, DEFAULT_CAMPAIGN_ID)
    settings = scripted_settings(database_path)
    return create_app(
        settings, database, ScriptedLLMClient(responses), None,
        degraded=degraded,
    )