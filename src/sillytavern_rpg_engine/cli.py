"""Maintenance CLI for the RPG engine: initialize, verify, and export
local campaign databases."""

import argparse
import sqlite3
import sys
from pathlib import Path

from .config import load_settings
from .domain.errors import DomainError
from .llm.openai import OpenAIChatClient
from .persistence.database import Database
from .persistence.migrations import MigrationRunner
from .server.app import create_app
from .services.audit_export import JsonlAuditExporter
from .services.campaign_export import CampaignExporter
from .services.campaign_import import CampaignImporter
from .services.database_backup import DatabaseBackupService
from .services.memory_events import MemoryIndexService


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="path to the SQLite campaign database",
    )


def _run_init_database(args: argparse.Namespace) -> int:
    database = Database(args.database)
    try:
        MigrationRunner(database).apply()
        with database.connect() as connection:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
    except sqlite3.Error as exc:
        print(f"init-db failed: {exc}", file=sys.stderr)
        return 1
    print(f"Schema version: {version}")
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    if not args.database.is_file():
        print(
            f"verify failed: database file does not exist: {args.database}",
            file=sys.stderr,
        )
        return 1
    database = Database(args.database)
    errors: list[str] = []
    try:
        with database.connect() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            if len(integrity) != 1 or integrity[0][0] != "ok":
                errors.append(
                    f"integrity check failed: {[row[0] for row in integrity]}"
                )
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                errors.append(
                    f"foreign key violations: "
                    f"{[tuple(row) for row in foreign_keys]}"
                )
    except sqlite3.Error as exc:
        print(f"verify failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        print(f"verify failed: {'; '.join(errors)}", file=sys.stderr)
        return 1
    print("integrity check: ok")
    print("foreign key check: ok")
    return 0


def _run_rebuild_memory_index(args: argparse.Namespace) -> int:
    database = Database(args.database)
    try:
        count = MemoryIndexService(database).rebuild()
    except (sqlite3.Error, RuntimeError) as exc:
        print(f"rebuild-memory-index failed: {exc}", file=sys.stderr)
        return 1
    print(f"Indexed {count} memory events")
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    try:
        settings = load_settings()
    except DomainError as exc:
        print(f"serve failed: {exc}", file=sys.stderr)
        return 1
    database = Database(args.database)
    degraded = False
    try:
        MigrationRunner(database).apply()
    except sqlite3.Error as exc:
        print(
            f"serve: migration failed ({exc}); starting in read-only"
            " diagnostic mode (download a copy via"
            " GET /admin/database/backup)",
            file=sys.stderr,
        )
        degraded = True
    narrator = OpenAIChatClient(settings.narrator)
    critic = OpenAIChatClient(settings.critic) if settings.critic else None
    app = create_app(settings, database, narrator, critic, degraded=degraded)
    import uvicorn

    uvicorn.run(
        app,
        host=args.host or settings.host,
        port=args.port or settings.port,
    )
    return 0


def _run_export(args: argparse.Namespace) -> int:
    database = Database(args.database)
    try:
        output = CampaignExporter(database).export(args.campaign, args.output)
    except (DomainError, sqlite3.Error, FileNotFoundError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    print(f"exported: {output}")
    return 0


def _run_backup(args: argparse.Namespace) -> int:
    database = Database(args.database)
    try:
        target = DatabaseBackupService(database).backup(
            args.dest_dir, keep=args.keep
        )
    except (sqlite3.Error, OSError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup: {target}")
    return 0


def _run_migrate_status(args: argparse.Namespace) -> int:
    database = Database(args.database)
    try:
        status = MigrationRunner(database).status()
    except sqlite3.Error as exc:
        print(f"migrate-status failed: {exc}", file=sys.stderr)
        return 1
    applied = ", ".join(str(version) for version in status.applied) or "(none)"
    print(f"applied: {applied}")
    print(f"pending: {len(status.pending)}")
    print(f"latest: {status.latest}")
    return 0


def _run_flush_audit(args: argparse.Namespace) -> int:
    database = Database(args.database)
    try:
        count = JsonlAuditExporter(database).flush(args.output)
    except (sqlite3.Error, FileNotFoundError) as exc:
        print(f"flush-audit failed: {exc}", file=sys.stderr)
        return 1
    print(f"flushed {count} audit events to {args.output}")
    return 0


def _run_import(args: argparse.Namespace) -> int:
    database = Database(args.database)
    try:
        MigrationRunner(database).apply()
        campaign_id = CampaignImporter(database).import_file(
            args.input, new_campaign_id=args.campaign, replace=args.replace
        )
    except (DomainError, sqlite3.Error, FileNotFoundError, ValueError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    print(f"imported: {campaign_id}")
    return 0


def _run_verify_export(args: argparse.Namespace) -> int:
    result = CampaignExporter(Database(args.input)).verify_export(args.input)
    if result["ok"]:
        print(f"verify-export: ok (campaign {result['campaign_id']})")
        return 0
    print(f"verify-export failed: {'; '.join(result['errors'])}", file=sys.stderr)
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sillytavern_rpg_engine",
        description="Local authoritative RPG state engine maintenance CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_database = subparsers.add_parser(
        "init-db", help="create or upgrade the schema of a campaign database"
    )
    _add_database_argument(init_database)
    init_database.set_defaults(handler=_run_init_database)

    verify = subparsers.add_parser(
        "verify", help="run integrity and foreign key checks on a database"
    )
    _add_database_argument(verify)
    verify.set_defaults(handler=_run_verify)

    rebuild = subparsers.add_parser(
        "rebuild-memory-index",
        help="rebuild the FTS5 memory event index from the event table",
    )
    _add_database_argument(rebuild)
    rebuild.set_defaults(handler=_run_rebuild_memory_index)

    export = subparsers.add_parser(
        "export", help="export one campaign to a JSON file"
    )
    _add_database_argument(export)
    export.add_argument("--campaign", required=True, help="campaign ID to export")
    export.add_argument(
        "--output", type=Path, required=True, help="destination JSON file"
    )
    export.set_defaults(handler=_run_export)

    backup = subparsers.add_parser(
        "backup", help="copy the database to a timestamped file with rotation"
    )
    _add_database_argument(backup)
    backup.add_argument(
        "--dest-dir", type=Path, required=True, help="directory for backups"
    )
    backup.add_argument(
        "--keep", type=int, default=7, help="number of backups to retain"
    )
    backup.set_defaults(handler=_run_backup)

    migrate_status = subparsers.add_parser(
        "migrate-status", help="show applied and pending schema migrations"
    )
    _add_database_argument(migrate_status)
    migrate_status.set_defaults(handler=_run_migrate_status)

    flush_audit = subparsers.add_parser(
        "flush-audit", help="append pending audit events to a JSONL file"
    )
    _add_database_argument(flush_audit)
    flush_audit.add_argument(
        "--output", type=Path, required=True, help="destination JSONL file"
    )
    flush_audit.set_defaults(handler=_run_flush_audit)

    import_cmd = subparsers.add_parser(
        "import", help="restore one campaign from a v2 export JSON file"
    )
    _add_database_argument(import_cmd)
    import_cmd.add_argument(
        "--input", type=Path, required=True, help="source export JSON file"
    )
    import_cmd.add_argument(
        "--campaign", default=None, help="override the campaign id"
    )
    import_cmd.add_argument(
        "--replace",
        action="store_true",
        help="replace an existing campaign with the same id",
    )
    import_cmd.set_defaults(handler=_run_import)

    verify_export = subparsers.add_parser(
        "verify-export", help="validate an exported campaign JSON file"
    )
    verify_export.add_argument(
        "--input", type=Path, required=True, help="export JSON file to verify"
    )
    verify_export.set_defaults(handler=_run_verify_export)

    serve = subparsers.add_parser(
        "serve", help="run the OpenAI-compatible API server"
    )
    _add_database_argument(serve)
    serve.add_argument("--host", default=None, help="bind host (env RPG_SERVER_HOST)")
    serve.add_argument(
        "--port", type=int, default=None,
        help="bind port (env RPG_SERVER_PORT)",
    )
    serve.set_defaults(handler=_run_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and run the selected subcommand; returns an exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
