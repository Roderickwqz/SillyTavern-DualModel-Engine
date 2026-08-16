# Pitfalls

Recurring issues and proactive-fix rules. IDs: `P001`, `P002`, ...

Do not edit manually. Use:

```bash
python .harness/scripts/memory.py add --text "<instruction>"
python .harness/scripts/memory.py relevant --path <repo-relative-file>
```

## P001 — Exclude ENGINE-only attributes from Narrator context

- Type: pitfall
- Scope: `src/sillytavern_rpg_engine/orchestration/graph.py`, `src/sillytavern_rpg_engine/orchestration/narrative.py`
- Tags: audience-filtering, hidden-attributes, narrator-context, engine-only, phase-7
- Status: active
- Added: 2026-08-16

Rule:
Re-filter retrieved attributes before assembling the Narrator prompt so attributes visible only to ENGINE never enter Narrator context.

Rationale:
PLAYER_UI filtering does not protect Narrator prompts because narrate retrieval currently requests both ENGINE and NARRATOR audiences and narrative prompt assembly does not re-filter the result.

Applies when:
- retrieving state for narration
- assembling Narrator prompt context
- changing attribute audience or visibility handling

Do not:
- assume exclusion from PLAYER_UI also excludes attributes from Narrator context
- pass ENGINE-only hidden attributes into Narrator prompts
