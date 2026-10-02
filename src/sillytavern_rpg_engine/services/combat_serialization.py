"""Combatant field serialization shared by snapshots and export/import."""

from __future__ import annotations

import json
from typing import Any

_COMBATANT_INSERT_COLUMNS = (
    "id", "encounter_id", "entity_id", "initiative",
    "action_used", "bonus_used", "reaction_used",
    "movement_total", "movement_used", "interaction_used",
    "slot_spent_this_turn", "attacks_this_turn",
    "hidden", "dodging", "disengaged",
    "help_grants_json", "readied_action_json", "debuffs_json",
    "mastery_uses_json", "concentrating_spell", "concentration_rounds",
    "recharge_json", "triggers_json",
)

COMBATANT_SELECT_SQL = ", ".join(_COMBATANT_INSERT_COLUMNS[2:])

COMBATANT_INSERT_SQL = (
    f"INSERT INTO combatants({', '.join(_COMBATANT_INSERT_COLUMNS)})"
    f" VALUES ({', '.join('?' for _ in _COMBATANT_INSERT_COLUMNS)})"
)


def combatant_row_to_dict(row) -> dict[str, Any]:
    """Serialize one combatants table row to a JSON-friendly dict."""
    readied = row["readied_action_json"]
    return {
        "entity_id": row["entity_id"],
        "initiative": row["initiative"],
        "action_used": bool(row["action_used"]),
        "bonus_used": bool(row["bonus_used"]),
        "reaction_used": bool(row["reaction_used"]),
        "movement_total": row["movement_total"],
        "movement_used": row["movement_used"],
        "interaction_used": bool(row["interaction_used"]),
        "slot_spent_this_turn": bool(row["slot_spent_this_turn"]),
        "attacks_this_turn": row["attacks_this_turn"],
        "hidden": bool(row["hidden"]),
        "dodging": bool(row["dodging"]),
        "disengaged": bool(row["disengaged"]),
        "help_grants": json.loads(row["help_grants_json"]),
        "readied_action": json.loads(readied) if readied else None,
        "debuffs": json.loads(row["debuffs_json"]),
        "mastery_uses": json.loads(row["mastery_uses_json"]),
        "concentrating_spell": row["concentrating_spell"],
        "concentration_rounds": row["concentration_rounds"],
        "recharge": json.loads(row["recharge_json"]),
        "triggers": json.loads(row["triggers_json"]),
    }


def normalize_combatant(combatant: dict[str, Any]) -> dict[str, Any]:
    """Fill missing combatant fields for legacy partial exports/snapshots."""
    defaults: dict[str, Any] = {
        "initiative": 0,
        "action_used": False,
        "bonus_used": False,
        "reaction_used": False,
        "movement_total": 0,
        "movement_used": 0,
        "interaction_used": False,
        "slot_spent_this_turn": False,
        "attacks_this_turn": 0,
        "hidden": False,
        "dodging": False,
        "disengaged": False,
        "help_grants": [],
        "readied_action": None,
        "debuffs": {},
        "mastery_uses": {},
        "concentrating_spell": None,
        "concentration_rounds": None,
        "recharge": [],
        "triggers": [],
    }
    return {**defaults, **combatant}


def combatant_insert_values(
    combatant_id: str,
    encounter_id: str,
    combatant: dict[str, Any],
) -> tuple[Any, ...]:
    """Build INSERT values for one combatants row."""
    row = normalize_combatant(combatant)
    readied = row["readied_action"]
    return (
        combatant_id,
        encounter_id,
        row["entity_id"],
        row["initiative"],
        int(row["action_used"]),
        int(row["bonus_used"]),
        int(row["reaction_used"]),
        row["movement_total"],
        row["movement_used"],
        int(row["interaction_used"]),
        int(row["slot_spent_this_turn"]),
        row["attacks_this_turn"],
        int(row["hidden"]),
        int(row["dodging"]),
        int(row["disengaged"]),
        json.dumps(row["help_grants"], sort_keys=True),
        json.dumps(readied, sort_keys=True) if readied is not None else None,
        json.dumps(row["debuffs"], sort_keys=True),
        json.dumps(row["mastery_uses"], sort_keys=True),
        row["concentrating_spell"],
        row["concentration_rounds"],
        json.dumps(row["recharge"], sort_keys=True),
        json.dumps(row["triggers"], sort_keys=True),
    )
