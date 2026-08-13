"""Scene entity scanning and entity reference resolution."""

from dataclasses import dataclass
import sqlite3

from ..domain.errors import AmbiguousEntityError, NotFoundError
from ..services.entities import normalize_key


@dataclass(frozen=True)
class SceneScan:
    """Entities mentioned in the player text, in order of first mention."""

    entity_ids: tuple[str, ...]
    names: dict[str, str]


def scan_scene(
    connection: sqlite3.Connection, campaign_id: str, text: str
) -> SceneScan:
    """Substring-match entity names and aliases, longest first, no overlap."""
    names = connection.execute(
        "SELECT id, name FROM entities WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchall()
    aliases = connection.execute(
        "SELECT entity_id, alias FROM entity_aliases WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchall()
    canonical = {row["id"]: row["name"] for row in names}
    candidates = [
        (row["name"].casefold(), row["id"]) for row in names
    ] + [
        (row["alias"].casefold(), row["entity_id"]) for row in aliases
    ]
    candidates = [c for c in candidates if c[0]]
    candidates.sort(key=lambda c: -len(c[0]))
    haystack = text.casefold()
    claimed: list[tuple[int, int]] = []
    found: dict[str, int] = {}
    for needle, entity_id in candidates:
        start = haystack.find(needle)
        while start != -1:
            end = start + len(needle)
            if not any(s < end and start < e for s, e in claimed):
                claimed.append((start, end))
                if entity_id not in found:
                    found[entity_id] = start
                break
            start = haystack.find(needle, start + 1)
    ordered = sorted(found.items(), key=lambda item: item[1])
    return SceneScan(
        entity_ids=tuple(entity_id for entity_id, _ in ordered),
        names={entity_id: canonical[entity_id] for entity_id, _ in ordered},
    )


def resolve_reference(
    connection: sqlite3.Connection, campaign_id: str, ref: str
) -> str:
    """Resolve one LLM- or user-supplied reference to a unique entity id."""
    if not isinstance(ref, str) or not ref.strip():
        raise NotFoundError("empty entity reference")
    ref = ref.strip()
    row = connection.execute(
        "SELECT id FROM entities WHERE campaign_id = ? AND id = ?",
        (campaign_id, ref),
    ).fetchone()
    if row is not None:
        return row["id"]
    normalized = normalize_key(ref)
    rows = connection.execute(
        "SELECT id FROM entities WHERE campaign_id = ?"
        " AND (normalized_name = ? OR id IN ("
        " SELECT entity_id FROM entity_aliases"
        " WHERE campaign_id = ? AND normalized_alias = ?))",
        (campaign_id, normalized, campaign_id, normalized),
    ).fetchall()
    if not rows:
        raise NotFoundError(f"entity {ref!r} not found in campaign {campaign_id}")
    if len(rows) > 1:
        raise AmbiguousEntityError(
            f"entity reference {ref!r} is ambiguous in campaign {campaign_id}"
        )
    return rows[0]["id"]
