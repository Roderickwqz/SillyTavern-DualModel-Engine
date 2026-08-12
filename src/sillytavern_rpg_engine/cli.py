"""Maintenance CLI for the RPG engine: initialize, verify, and export
local campaign databases."""

import argparse
import sqlite3
import sys
from pathlib import Path

from .domain.errors import DomainError
from .persistence.database import Database
from .persistence.migrations import MigrationRunner
from .services.campaign_export import CampaignExporter
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


def _run_export(args: argparse.Namespace) -> int:
    database = Database(args.database)
    try:
        output = CampaignExporter(database).export(args.campaign, args.output)
    except (DomainError, sqlite3.Error, FileNotFoundError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    print(f"exported: {output}")
    return 0


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

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and run the selected subcommand; returns an exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
