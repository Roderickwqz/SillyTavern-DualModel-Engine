# Third-Party Notices

## RPG Companion for SillyTavern

Copyright (C) Marinara and contributors. Licensed under AGPL-3.0.
Source: https://github.com/SpicyMarinara/rpg-companion-sillytavern @ e021946867b9a457a3f8b67c78642f9b92ebb90d

### Modifications in this fork

- Added `src/compat/*` modules for LangGraph authoritative Tracker rendering.
- Forced Together mode; disabled Separate / External API / Auto Update / History Persistence.
- Read-only authoritative tracker values; dynamic `attributes` renderer.
- Narrowed Together-mode JSON cleaning to tracker-shaped blocks only.
- Rules metadata panel routing (`rules.mode`).
- Pending proposal surfacing and stale-parse warnings.
