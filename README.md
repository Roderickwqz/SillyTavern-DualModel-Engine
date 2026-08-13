# SillyTavern DualModel Engine

DualModel Engine is a Git-installable SillyTavern extension for local, one-to-one roleplay. It keeps versioned narrative state with a separate Recorder model, restores state per swipe, and optionally provides code-authoritative D20 and declarative custom rules.

## Requirements and boundaries

- SillyTavern **1.18.0 or later**; this extension is for one local user, one active browser tab, and one-to-one character chats.
- Group chats are detected and remain read-only: the extension never writes DualModel Engine data to them.
- No separate extension backend, database, remote storage, credential form, or runtime CDN is used. The extension reuses SillyTavern's same-origin storage API; installers receive the committed bundle and do not run npm at runtime.
- The Narrator always uses SillyTavern's current main connection. The Recorder uses a selected Connection Profile and never changes the Narrator connection.

## Install and configure

1. In SillyTavern, open **Extensions** and choose **Install extension** (Git URL).
2. Paste `https://github.com/Roderickwqz/SillyTavern-DualModel-Engine` and install. SillyTavern loads `dist/index.js` and `dist/style.css` from the repository.
3. Enable the **Connection Manager** extension, then create a Connection Profile for the Recorder model. Keys, base URL, proxy configuration, and model credentials remain in SillyTavern; do not enter them in this extension.
4. In DualModel Engine settings, select that Recorder Profile, choose a mode, and enable the extension for a supported one-to-one chat. The global and character defaults apply only when a chat is first initialized.

Modes are `narrative`, `d20-lite`, and imported `custom` presets. To import a custom preset, open the Rules tab, choose **Import preset**, select a JSON file, review the displayed summary, and confirm; only declarative JSON presets are accepted. Existing chats pin their preset ID and version. To switch an established chat, use the explicit export → confirm → reset flow; it removes only this extension's saved namespace and leaves message text and other extensions' data alone.

## D20, tools, and other dice extensions

Use **Probe tools** after configuring the Narrator/proxy. When tool calling is available, `DualModelResolveD20Check` is the authoritative formal-check tool. A failed tool probe automatically downgrades `automatic-tool` to `enforced-preflight` and shows the reason; if preflight is unavailable, formal D20 is disabled while narrative state can continue.

Official D&D Dice can coexist for menus and `/roll`. It is not this extension's authority or dependency. If both function tools are enabled, disable the official extension's Function Tool so the Narrator does not select an unbound roll tool.

## Recovery and data ownership

Before destructive recovery, preset replacement, or diagnosis, export raw DualModel Engine data from the chat panel. Swipe selection restores the selected branch; edits and deletions mark later data stale and offer confirmed recalculation from the last valid snapshot. Recorder errors, invalid patches, stale results, cancellation, and save failures preserve the last valid state.

Disabling the extension stops state injection and background tasks. It **does not delete** data already saved in a chat. Re-enable it or export the raw namespace to recover it later. See [data format documentation](docs/data-format.md) and [developer testing instructions](docs/testing.md).

## Development

Requires Node.js `>=20.19.0`.

```bash
npm ci
npm run check
```

The complete manual-host procedure is in [tests/e2e/manual-model-checklist.md](tests/e2e/manual-model-checklist.md).

## RPG Engine Core (Phase 1)

Phase 1 is a local state library plus maintenance CLI, not yet a SillyTavern API. It owns a SQLite campaign database with migrations, campaigns, entities, dynamic attributes, projections, change proposals, and audit/export tooling. The existing JavaScript DualModel Engine remains untouched, but it must not be used as a second authority once the later LangGraph API is enabled.

```bash
python -m pip install -e '.[dev]'
mkdir -p data
python -m sillytavern_rpg_engine init-db --database ./data/campaigns.sqlite3
python -m sillytavern_rpg_engine verify --database ./data/campaigns.sqlite3
python -m pytest tests/backend -q
```

## RPG Engine Core (Phase 2)

Phase 2 adds long memory and personality on top of the Phase 1 core: temporally valid facts (superseded, never deleted), permanent audience-filtered memory events with FTS5 trigram search, directed relationships, capped and inertia-checked trait change events, provenance-linked development arcs and summaries, and a tiered retrieval service for future prompt assembly. Trait deltas are capped per tier (normal ≤ 3, important ≤ 8, major ≤ 20) and by a 25-point inertia window over the last 5 events. The FTS index is derivable and can be rebuilt at any time:

```bash
python -m sillytavern_rpg_engine rebuild-memory-index --database ./data/campaigns.sqlite3
```

Authoritative entity/attribute state remains campaign-global; branch-scoped divergence arrives with the Phase 6 branching plan.

## RPG Engine Core (Phase 3)

Phase 3 adds the D&D 2024 / 5.5e rules layer: per-campaign opt-in (`DndRulesService.seed_pack` → fill character data → `enable`, which fails atomically with a missing-fields report when any character is incomplete), authoritative dice with injectable randomness (`DiceRoller`; production uses `secrets`, tests use scripted sequences), and immutable `dice_rolls` records pinned to the campaign's rules version (`srd-5.2.1`). Combat covers initiative with 2024 surprise (Disadvantage), round/turn lifecycle, action/bonus-action/reaction/movement budgets, movement costs (difficult terrain, crawl, stand up, forced, mounted), attack resolution with aggregated advantage/disadvantage, cover, criticals, weapon properties (Light, Loading, Thrown, Reach, Finesse), grapple/shove saves, all eight 2024 Weapon Mastery properties (Cleave/Nick once per turn), the full damage pipeline (immunity → resistance halving → vulnerability doubling, temp HP, 0 HP dying, death saves, massive damage), 2024 conditions including 6-level Exhaustion (−2/level on d20 Tests, −5 ft/level), spell-slot transactions (one slot per turn, concentration with damage checks and duration countdown), short/long rests, monster multiattack, Recharge, and data-driven turn triggers. Narrative campaigns never execute D&D resolution; every rules-gated operation validates the pinned rules version.

## OpenAI-compatible API server

Install with API dependencies and start the server:

```bash
python -m pip install -e ".[dev]"
python -m sillytavern_rpg_engine serve --database ./rpg.sqlite3
```

SillyTavern → API Connection → Custom OpenAI Compatible:

- Base URL: `http://127.0.0.1:8000/v1`
- Custom Body: `{"campaign_id": "campaign-001"}`

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RPG_NARRATOR_MODEL` | (required) | Narrator model id |
| `RPG_NARRATOR_BASE_URL` | `http://127.0.0.1:5000/v1` | Narrator endpoint |
| `RPG_NARRATOR_API_KEY` | empty | Narrator key |
| `RPG_NARRATOR_TEMPERATURE` | `0.8` | Sampling temperature |
| `RPG_NARRATOR_TIMEOUT_SECONDS` | `120` | Upstream timeout |
| `RPG_NARRATOR_MAX_TOKENS` | `1024` | Reply cap |
| `RPG_CRITIC_MODEL` | unset | Critic model id; unset = single-model mode |
| `RPG_CRITIC_BASE_URL` / `RPG_CRITIC_API_KEY` | inherit narrator | Critic endpoint |
| `RPG_CRITIC_TEMPERATURE` / `RPG_CRITIC_TIMEOUT_SECONDS` / `RPG_CRITIC_MAX_TOKENS` | `0.8` / `120` / `1024` | Critic sampling |
| `RPG_SERVER_HOST` / `RPG_SERVER_PORT` | `127.0.0.1` / `8000` | Bind address |
| `RPG_DATABASE` | `./rpg.sqlite3` | Default database path |
| `RPG_MAX_HISTORY_MESSAGES` | `40` | Prompt history cap |

Endpoints: `GET /v1/models`, `POST /v1/chat/completions` (non-streaming),
`GET /health` (`?deep=1` pings upstreams), `GET /admin/campaigns/{id}/export`.

Chat commands: `确认提案 <id>` / `reject <id>` manage pending proposals;
`查询…` is read-only; explicit changes (`把X调整为Y`) apply immediately.
