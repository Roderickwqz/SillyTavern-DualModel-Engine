# LangGraph RPG Core — Phase 5 (RPG Companion Compat Fork & Dynamic Tracker Rendering) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the AGPL-compatible RPG Companion fork that renders backend-authoritative Tracker JSON in Together mode only: dynamic `attributes` by category/display, rules-metadata panel routing, read-only authoritative UI, narrowed JSON block cleaning, pending-proposal surfacing, and stale-parse warnings — without reintroducing DualModel Engine dual-writes.

**Architecture:** Phase 4's Python `TrackerPresenter` becomes the single source of Tracker shape; this phase extends it to populate `userStats.attributes`, `infoBox`, character `relationship`, optional `combat`, and root `state_version`. The browser extension is a vendored fork of [SpicyMarinara/rpg-companion-sillytavern](https://github.com/SpicyMarinara/rpg-companion-sillytavern) pinned to a fixed upstream SHA, with additive `src/compat/*` modules and minimal `// COMPAT:` patches in upstream files. SillyTavern still talks only to the FastAPI backend; the extension parses the fenced Tracker block from each assistant reply and renders it read-only. Per-swipe branch recovery stays Phase 6.

**Tech Stack:** Python 3.11+ (`TrackerPresenter`, `ProjectionService`, pytest), upstream RPG Companion 3.7.4 (plain ESM, no bundler required), Vitest 4.x + jsdom for compat unit/integration tests, SillyTavern ≥ 1.18.0 as manual E2E host.

## Global Constraints

- LangGraph + SQLite remain the only authoritative state (spec §2.1). RPG Companion per-swipe caches are display-only; panel edits must not call `saveChatData` / `updateMessageSwipeData` for tracker field mutation in compat mode.
- DualModel Engine (`src/index.js`, Recorder pipeline) must not run alongside this extension (spec §2.2). README must say "install RPG Companion Compat, point Chat Completion to `http://127.0.0.1:8000/v1`, disable DualModel Engine."
- Together mode only: `generationMode = 'together'`; Separate, External API, Auto Update, and History Persistence are forced off and their settings hidden (spec §12.1).
- Tracker output must include root `rules` metadata (`mode`, `enabled`, `version`, optional `custom_preset_id`). Frontend panel routing reads this; changing it in the UI cannot change backend Campaign rules (spec §12.1).
- Dynamic `attributes` arrays use backend `AttributeDefinition` shape: `key`, `label`, `category`, `type`, `value`, `max`, `display`. Unknown `display` values render as read-only text; data is never dropped (spec §12.2).
- Read-only authoritative mode: no `contenteditable` on tracker values; collapse/theme/layout/history viewing stay (spec §12.3).
- JSON cleaning removes only fenced JSON blocks whose parsed object contains at least one of `userStats` or `infoBox` (same structural rule as backend `normalize.strip_tracker_blocks`). Ordinary narrative code blocks must survive (spec §12.4).
- Parse failure shows a stale warning banner; backend state is never rolled back from panel cache (spec §12.3, §16).
- Pending proposals are listed from root `pending_proposals`; approval/rejection happens via chat commands handled by Phase 4 gate (`确认提案 <id>` / `拒绝提案 <id>`), not inline panel writes.
- AGPL-3.0 compliance: retain upstream `LICENSE`, add `THIRD_PARTY_NOTICES.md` and `UPSTREAM.md` with pinned SHA and modification list; publish corresponding source (spec §2.3).
- Scene projection convention (Phase 5): entity id `_scene` holds `category=scene` attributes mapped to `infoBox` keys (`location`, `date`, `time`, `weather`, `temperature`, `recent_events`). Player entity id `player` if present, else first `character` entity, receives `userStats.attributes`. Other characters keep per-character `attributes`.
- Conda env `py313`; Python: `python -m pip install -e ".[dev]"`; `python -m pytest tests/backend -q`; JS: `npm run test:run`; guard: `python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend`; commit via `bash .harness/scripts/committer "<msg>" <files...>`.

## Multi-Plan Roadmap

1. Phase 1 (done): authoritative Python core and persistence.
2. Phase 2 (done): memory and personality.
3. Phase 3 (done): D&D 2024 / 5.5e rules layer.
4. Phase 4 (done): LangGraph turn orchestration and OpenAI-compatible API.
5. **This plan:** RPG Companion compat fork and dynamic Tracker rendering.
6. Branching and release: swipe/edit/delete recovery, backups, long-run simulation, real SillyTavern E2E.

## File Structure

```text
extensions/rpg-companion-compat/          # NEW: vendored upstream + compat layer
├── LICENSE                             # upstream AGPL-3.0 (unchanged)
├── UPSTREAM.md                         # NEW: pin SHA, fetch instructions, patch list
├── THIRD_PARTY_NOTICES.md              # NEW: attribution + modification summary
├── manifest.json                       # MODIFY: display_name, version suffix
├── index.js                            # MODIFY: compat bootstrap hooks
├── settings.html                       # MODIFY: hide disabled modes, add diagnostics strip
├── style.css                           # MODIFY: dynamic attribute + stale banner styles
├── src/
│   ├── compat/                         # NEW: all compat-specific modules
│   │   ├── trackerSchema.js
│   │   ├── trackerCleaning.js
│   │   ├── readOnly.js
│   │   ├── attributes.js
│   │   ├── rulesPanel.js
│   │   ├── dndPanel.js
│   │   ├── pendingProposals.js
│   │   ├── staleWarning.js
│   │   └── bootstrap.js
│   ├── core/state.js                   # MODIFY: compat defaults + settingsVersion bump
│   ├── systems/generation/parser.js    # MODIFY: preserve rules/state_version/pending_proposals
│   ├── systems/features/jsonCleaning.js # MODIFY: narrowed regex via trackerCleaning
│   ├── systems/rendering/userStats.js  # MODIFY: dynamic attributes + readOnly guard
│   ├── systems/rendering/infoBox.js    # MODIFY: readOnly guard
│   ├── systems/rendering/thoughts.js   # MODIFY: dynamic attributes + readOnly guard
│   └── systems/generation/injector.js  # MODIFY: no-op separate/external in compat mode
├── src/... (remaining upstream files vendored verbatim)
scripts/
└── vendor-rpg-companion.sh             # NEW: reproducible upstream vendor script
src/sillytavern_rpg_engine/
└── orchestration/presenter.py            # MODIFY: full Tracker projection
tests/backend/unit/
└── test_presenter.py                   # MODIFY: scene/infoBox/combat/relationship cases
tests/rpg-companion-compat/
├── unit/
│   ├── trackerSchema.test.js
│   ├── trackerCleaning.test.js
│   ├── attributes.test.js
│   ├── readOnly.test.js
│   ├── rulesPanel.test.js
│   ├── pendingProposals.test.js
│   └── parserCompat.test.js
└── integration/
    └── tracker-roundtrip.test.js
vitest.config.js                        # MODIFY: include tests/rpg-companion-compat
README.md                               # MODIFY: install + wiring for compat extension
```

---

### Task 1: Vendor upstream RPG Companion with license compliance

**Files:**
- Create: `scripts/vendor-rpg-companion.sh`
- Create: `extensions/rpg-companion-compat/UPSTREAM.md`
- Create: `extensions/rpg-companion-compat/THIRD_PARTY_NOTICES.md`
- Create: `extensions/rpg-companion-compat/manifest.json` (via vendor script + patch)
- Test: `tests/rpg-companion-compat/unit/manifest.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces: vendored tree at `extensions/rpg-companion-compat/`; `UPSTREAM_SHA=e021946867b9a457a3f8b67c78642f9b92ebb90d`; `manifest.json` with `display_name: "RPG Companion Compat (LangGraph)"`, `version: "3.7.4-compat.1"`.

- [x] **Step 1: Write the failing test**

```javascript
// tests/rpg-companion-compat/unit/manifest.test.js
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('RPG Companion compat manifest', () => {
    it('is pinned and renamed for the LangGraph fork', () => {
        const manifest = JSON.parse(
            readFileSync('extensions/rpg-companion-compat/manifest.json', 'utf8'),
        );
        expect(manifest.display_name).toContain('Compat');
        expect(manifest.js).toBe('index.js');
        expect(manifest.version).toMatch(/-compat\./);
    });

    it('records upstream pin in UPSTREAM.md', () => {
        const upstream = readFileSync('extensions/rpg-companion-compat/UPSTREAM.md', 'utf8');
        expect(upstream).toContain('e021946867b9a457a3f8b67c78642f9b92ebb90d');
        expect(upstream).toContain('SpicyMarinara/rpg-companion-sillytavern');
    });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/manifest.test.js`
Expected: FAIL — manifest / UPSTREAM.md not found

- [x] **Step 3: Add vendor script and notices**

```bash
#!/usr/bin/env bash
# scripts/vendor-rpg-companion.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/extensions/rpg-companion-compat"
UPSTREAM_REPO="https://github.com/SpicyMarinara/rpg-companion-sillytavern.git"
UPSTREAM_SHA="e021946867b9a457a3f8b67c78642f9b92ebb90d"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 "$UPSTREAM_REPO" "$TMP/upstream"
git -C "$TMP/upstream" fetch --depth 1 origin "$UPSTREAM_SHA"
git -C "$TMP/upstream" checkout "$UPSTREAM_SHA"
rm -rf "$DEST"
mkdir -p "$DEST"
rsync -a --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  "$TMP/upstream/" "$DEST/"
python3 - <<'PY'
import json, pathlib
root = pathlib.Path("extensions/rpg-companion-compat")
manifest = json.loads((root / "manifest.json").read_text())
manifest["display_name"] = "RPG Companion Compat (LangGraph)"
manifest["version"] = f"{manifest['version']}-compat.1"
(root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
PY
```

`extensions/rpg-companion-compat/UPSTREAM.md`:

```markdown
# Upstream RPG Companion

- Repository: https://github.com/SpicyMarinara/rpg-companion-sillytavern
- Pinned commit: e021946867b9a457a3f8b67c78642f9b92ebb90d
- Upstream version: 3.7.4
- License: AGPL-3.0

## Refresh procedure

1. Run `bash scripts/vendor-rpg-companion.sh`
2. Re-apply patches listed in `THIRD_PARTY_NOTICES.md`
3. Run `npm run test:run -- tests/rpg-companion-compat`
```

`extensions/rpg-companion-compat/THIRD_PARTY_NOTICES.md`:

```markdown
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
```

Run: `bash scripts/vendor-rpg-companion.sh`

- [x] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/manifest.test.js`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "chore: vendor RPG Companion compat fork" \
  scripts/vendor-rpg-companion.sh \
  extensions/rpg-companion-compat/UPSTREAM.md \
  extensions/rpg-companion-compat/THIRD_PARTY_NOTICES.md \
  extensions/rpg-companion-compat/manifest.json \
  extensions/rpg-companion-compat/LICENSE \
  extensions/rpg-companion-compat/index.js \
  extensions/rpg-companion-compat/settings.html \
  extensions/rpg-companion-compat/style.css \
  extensions/rpg-companion-compat/src \
  tests/rpg-companion-compat/unit/manifest.test.js
```

---

### Task 2: Tracker schema utilities

**Files:**
- Create: `extensions/rpg-companion-compat/src/compat/trackerSchema.js`
- Test: `tests/rpg-companion-compat/unit/trackerSchema.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces: `TRACKER_ROOT_KEYS`; `TRACKER_SIGNATURE_KEYS`; `isTrackerPayload(obj: unknown): boolean`; `normalizeTrackerPayload(obj: object): { rules, userStats, infoBox, characters, pending_proposals, state_version, combat }`.

- [x] **Step 1: Write the failing test**

```javascript
// tests/rpg-companion-compat/unit/trackerSchema.test.js
import { describe, expect, it } from 'vitest';
import {
    isTrackerPayload,
    normalizeTrackerPayload,
    TRACKER_ROOT_KEYS,
} from '../../../extensions/rpg-companion-compat/src/compat/trackerSchema.js';

describe('trackerSchema', () => {
    it('recognizes backend tracker root keys', () => {
        expect(TRACKER_ROOT_KEYS).toContain('userStats');
        expect(TRACKER_ROOT_KEYS).toContain('rules');
        expect(isTrackerPayload({ userStats: {}, rules: { mode: 'narrative' } })).toBe(true);
        expect(isTrackerPayload({ foo: 1 })).toBe(false);
        expect(isTrackerPayload('text')).toBe(false);
    });

    it('normalizes partial payloads with safe defaults', () => {
        const normalized = normalizeTrackerPayload({
            rules: { mode: 'dnd-2024', enabled: true, version: '2024' },
            characters: [{ name: '艾琳', attributes: [] }],
            pending_proposals: [{ id: 'p1' }],
            state_version: 3,
        });
        expect(normalized.rules.mode).toBe('dnd-2024');
        expect(normalized.userStats.attributes).toEqual([]);
        expect(normalized.characters).toHaveLength(1);
        expect(normalized.pending_proposals).toHaveLength(1);
        expect(normalized.state_version).toBe(3);
    });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/trackerSchema.test.js`
Expected: FAIL — module not found

- [x] **Step 3: Implement trackerSchema.js**

```javascript
// extensions/rpg-companion-compat/src/compat/trackerSchema.js

/** Root keys that identify a LangGraph Tracker payload (spec §12.2 + extensions). */
export const TRACKER_ROOT_KEYS = Object.freeze([
    'rules',
    'userStats',
    'infoBox',
    'characters',
    'pending_proposals',
    'state_version',
    'combat',
]);

/** Keys used together with userStats/infoBox to detect tracker JSON blocks. */
export const TRACKER_SIGNATURE_KEYS = Object.freeze(['userStats', 'infoBox']);

const DEFAULT_RULES = Object.freeze({
    mode: 'narrative',
    enabled: false,
    version: null,
});

const DEFAULT_USER_STATS = Object.freeze({
    stats: [],
    status: {},
    skills: [],
    inventory: {},
    quests: {},
    attributes: [],
});

/**
 * @param {unknown} value
 * @returns {value is Record<string, unknown>}
 */
export function isTrackerPayload(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return false;
    }
    return TRACKER_SIGNATURE_KEYS.some((key) => key in value);
}

/**
 * @param {Record<string, unknown>} raw
 */
export function normalizeTrackerPayload(raw) {
    const rules = { ...DEFAULT_RULES, ...(raw.rules ?? {}) };
    const userStats = {
        ...DEFAULT_USER_STATS,
        ...(raw.userStats ?? {}),
        attributes: Array.isArray(raw.userStats?.attributes)
            ? raw.userStats.attributes
            : [],
    };
    const infoBox = { ...(raw.infoBox ?? {}) };
    const characters = Array.isArray(raw.characters) ? raw.characters : [];
    const pending_proposals = Array.isArray(raw.pending_proposals)
        ? raw.pending_proposals
        : [];
    const state_version = Number.isInteger(raw.state_version)
        ? raw.state_version
        : null;
    const combat = raw.combat ?? null;
    return {
        rules,
        userStats,
        infoBox,
        characters,
        pending_proposals,
        state_version,
        combat,
    };
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/trackerSchema.test.js`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add compat tracker schema helpers" \
  extensions/rpg-companion-compat/src/compat/trackerSchema.js \
  tests/rpg-companion-compat/unit/trackerSchema.test.js
```

---

### Task 3: Parser preserves compat root fields

**Files:**
- Modify: `extensions/rpg-companion-compat/src/systems/generation/parser.js`
- Create: `tests/rpg-companion-compat/unit/parserCompat.test.js`

**Interfaces:**
- Consumes: `isTrackerPayload`, `normalizeTrackerPayload` from `src/compat/trackerSchema.js`.
- Produces: `parseResponse` returns `compat` alongside legacy fields: `{ userStats, infoBox, characterThoughts, compat }` where `compat` is the normalized Tracker or `null`.

- [x] **Step 1: Write the failing test**

```javascript
// tests/rpg-companion-compat/unit/parserCompat.test.js
import { describe, expect, it } from 'vitest';
import { parseResponse } from '../../../extensions/rpg-companion-compat/src/systems/generation/parser.js';

const SAMPLE = [
    '剧情段落。',
    '```json',
    JSON.stringify({
        rules: { mode: 'narrative', enabled: false, version: null },
        userStats: { stats: [], status: {}, skills: [], inventory: {}, quests: {}, attributes: [] },
        infoBox: { location: '银月城' },
        characters: [{ name: '艾琳', attributes: [{ key: 'alchemy', label: '炼金术', category: 'skill', type: 'number', value: 35, max: 100, display: 'bar' }] }],
        pending_proposals: [{ id: 'prop-1', reason: '剧情推断' }],
        state_version: 7,
    }, null, 2),
    '```',
].join('\n');

describe('parseResponse compat', () => {
    it('extracts normalized compat payload from fenced tracker JSON', () => {
        const result = parseResponse(SAMPLE);
        expect(result.compat).not.toBeNull();
        expect(result.compat.rules.mode).toBe('narrative');
        expect(result.compat.infoBox.location).toBe('银月城');
        expect(result.compat.characters[0].name).toBe('艾琳');
        expect(result.compat.pending_proposals).toHaveLength(1);
        expect(result.compat.state_version).toBe(7);
    });

    it('does not treat arbitrary JSON code blocks as tracker', () => {
        const result = parseResponse('```json\n{"answer": 42}\n```');
        expect(result.compat).toBeNull();
    });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/parserCompat.test.js`
Expected: FAIL — `result.compat` undefined

- [x] **Step 3: Patch parser.js**

At top of `extensions/rpg-companion-compat/src/systems/generation/parser.js`, add:

```javascript
// COMPAT: LangGraph authoritative tracker support
import { isTrackerPayload, normalizeTrackerPayload } from '../../compat/trackerSchema.js';
```

In `parseResponse`, initialize:

```javascript
result.compat = null;
```

Inside the unified-structure branch (where `unwrapped.userStats` is detected), after populating legacy fields, add:

```javascript
if (isTrackerPayload(unwrapped)) {
    result.compat = normalizeTrackerPayload(unwrapped);
}
```

In the fenced ```json block loop, after `normalizedParsed` is obtained, add the same `isTrackerPayload` / `normalizeTrackerPayload` assignment before `continue`.

At every `return result` in `parseResponse`, ensure `compat` key exists (default `null`).

- [x] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/parserCompat.test.js`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: preserve compat tracker fields in parser" \
  extensions/rpg-companion-compat/src/systems/generation/parser.js \
  tests/rpg-companion-compat/unit/parserCompat.test.js
```

---

### Task 4: Narrowed Together-mode JSON cleaning

**Files:**
- Create: `extensions/rpg-companion-compat/src/compat/trackerCleaning.js`
- Modify: `extensions/rpg-companion-compat/src/systems/features/jsonCleaning.js`
- Test: `tests/rpg-companion-compat/unit/trackerCleaning.test.js`

**Interfaces:**
- Consumes: `isTrackerPayload` from `trackerSchema.js`.
- Produces: `stripTrackerJsonBlocks(text: string): string`; `buildTrackerCleaningRegex(): string` returning a regex source that only matches tracker-shaped fenced JSON.

- [x] **Step 1: Write the failing test**

```javascript
// tests/rpg-companion-compat/unit/trackerCleaning.test.js
import { describe, expect, it } from 'vitest';
import { stripTrackerJsonBlocks } from '../../../extensions/rpg-companion-compat/src/compat/trackerCleaning.js';

describe('stripTrackerJsonBlocks', () => {
    it('removes tracker-shaped json fences only', () => {
        const input = [
            '故事继续。',
            '```json',
            '{"rules":{"mode":"narrative"},"userStats":{"attributes":[]},"infoBox":{}}',
            '```',
            '代码示例：',
            '```json',
            '{"snippet":"keep me"}',
            '```',
        ].join('\n');
        const output = stripTrackerJsonBlocks(input);
        expect(output).toContain('故事继续。');
        expect(output).toContain('"snippet":"keep me"');
        expect(output).not.toContain('"userStats"');
    });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/trackerCleaning.test.js`
Expected: FAIL

- [x] **Step 3: Implement cleaning + wire jsonCleaning.js**

```javascript
// extensions/rpg-companion-compat/src/compat/trackerCleaning.js
import { isTrackerPayload } from './trackerSchema.js';

const JSON_FENCE = /```json\s*\n([\s\S]*?)```/gim;

export function stripTrackerJsonBlocks(text) {
    if (!text) return text;
    return text.replace(JSON_FENCE, (full, inner) => {
        try {
            const parsed = JSON.parse(inner.trim());
            return isTrackerPayload(parsed) ? '' : full;
        } catch {
            return full;
        }
    }).replace(/\n{3,}/g, '\n\n').trim();
}

/** SillyTavern regex scripts cannot call JS — use a conservative pattern for display cleaning. */
export function buildTrackerCleaningRegex() {
    // Matches fenced JSON containing userStats and/or infoBox object keys.
    return String.raw`/```json\s*\n[\s\S]*?"(?:userStats|infoBox)"\s*:[\s\S]*?```/gim`;
}
```

In `jsonCleaning.js`, replace `newPattern` assignment with:

```javascript
import { buildTrackerCleaningRegex } from '../../compat/trackerCleaning.js';
// ...
const newPattern = buildTrackerCleaningRegex();
```

Update `scriptName` to `'RPG Companion Compat - Remove Tracker JSON (Together Mode)'`.

- [x] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/trackerCleaning.test.js`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "fix: narrow tracker JSON cleaning to compat blocks" \
  extensions/rpg-companion-compat/src/compat/trackerCleaning.js \
  extensions/rpg-companion-compat/src/systems/features/jsonCleaning.js \
  tests/rpg-companion-compat/unit/trackerCleaning.test.js
```

---

### Task 5: Backend TrackerPresenter full projection

**Files:**
- Modify: `src/sillytavern_rpg_engine/orchestration/presenter.py`
- Modify: `tests/backend/unit/test_presenter.py`

**Interfaces:**
- Consumes: `ProjectionService.for_audience(campaign_id, branch_id, Audience.PLAYER_UI)`; existing `ProposalService.pending`.
- Produces: `TrackerPresenter.build` returns spec §12.2 shape plus `state_version: int`, optional `combat: dict | null`, populated `userStats.attributes`, `infoBox`, character `relationship`, and `details` from visible facts. Helper functions: `_attribute_dict(row, value)`, `_resolve_player_entity_id(entities)`, `_build_info_box(definitions, values)`, `_relationship_badge(relationships, player_id, entity_id)`.

- [x] **Step 1: Write the failing tests**

Append to `tests/backend/unit/test_presenter.py`:

```python
from sillytavern_rpg_engine.domain.models import RulesMode
from sillytavern_rpg_engine.services.campaigns import SetCampaignRulesOperation


def test_scene_attributes_map_to_info_box(database):
    campaigns, attributes = _world(database)
    attributes.apply_explicit("c1", "main", 2, CreateEntityOperation(
        entity_id="_scene", kind=EntityKind.LOCATION, name="场景",
    ))
    attributes.apply_explicit("c1", "main", 3, DefineAttributeOperation(AttributeDefinition(
        campaign_id="c1", key="location", label="地点", category="scene",
        value_type=AttributeType.TEXT, display=DisplayType.TEXT,
        audiences=frozenset({Audience.PLAYER_UI}),
    )))
    attributes.apply_explicit("c1", "main", 4, SetAttributeOperation(
        "_scene", "location", "银月城", None,
    ))
    tracker = TrackerPresenter(database).build("c1", "main")
    assert tracker["infoBox"]["location"] == "银月城"
    assert tracker["state_version"] == 5


def test_player_attributes_render_under_user_stats(database):
    campaigns, attributes = _world(database)
    attributes.apply_explicit("c1", "main", 2, CreateEntityOperation(
        entity_id="player", kind=EntityKind.CHARACTER, name="旅人",
    ))
    attributes.apply_explicit("c1", "main", 3, DefineAttributeOperation(AttributeDefinition(
        campaign_id="c1", key="health", label="生命", category="resource",
        value_type=AttributeType.NUMBER, display=DisplayType.BAR,
        audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
    )))
    attributes.apply_explicit("c1", "main", 4, SetAttributeOperation(
        "player", "health", 80, None,
    ))
    tracker = TrackerPresenter(database).build("c1", "main")
    keys = [a["key"] for a in tracker["userStats"]["attributes"]]
    assert keys == ["health"]
    assert tracker["userStats"]["attributes"][0]["value"] == 80


def test_relationship_badge_on_characters(database):
    from sillytavern_rpg_engine.services.relationships import SetRelationshipOperation
    campaigns, attributes = _world(database)
    attributes.apply_explicit("c1", "main", 2, CreateEntityOperation(
        entity_id="player", kind=EntityKind.CHARACTER, name="旅人",
    ))
    attributes.apply_explicit("c1", "main", 3, SetRelationshipOperation(
        from_entity_id="erin", to_entity_id="player",
        dimension="status", value="Ally",
        audiences=frozenset({Audience.PLAYER_UI}),
    ))
    tracker = TrackerPresenter(database).build("c1", "main")
    erin = next(c for c in tracker["characters"] if c["name"] == "艾琳")
    assert erin["relationship"] == {"status": "Ally"}
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/unit/test_presenter.py -q`
Expected: FAIL on new assertions (`infoBox` empty, `state_version` missing, etc.)

- [x] **Step 3: Implement presenter enhancements**

Replace `presenter.py` `build` body with projection-driven assembly:

```python
"""Tracker JSON presentation built from committed, player-visible state."""

import json
from typing import Any

from ..domain.models import Audience, EntityKind
from ..persistence.database import Database
from ..services.projection import ProjectionService
from ..services.proposals import ProposalService
from ..services.mutations import MutationEngine

_PLAYER_ENTITY_ID = "player"
_SCENE_ENTITY_ID = "_scene"
_INFOBOX_ATTR_KEYS = frozenset({
    "location", "date", "time", "weather", "temperature", "recent_events",
})


def _attribute_dict(definition: dict[str, Any], value: Any) -> dict[str, Any]:
    return {
        "key": definition["key"],
        "label": definition["label"],
        "category": definition["category"],
        "type": definition["value_type"],
        "value": value,
        "max": definition["maximum"],
        "display": definition["display"],
    }


def _resolve_player_entity_id(entities: list[dict[str, Any]]) -> str | None:
    for entity in entities:
        if entity["id"] == _PLAYER_ENTITY_ID:
            return entity["id"]
    for entity in entities:
        if entity["kind"] == EntityKind.CHARACTER.value:
            return entity["id"]
    return None


def _build_info_box(
    definitions: dict[str, dict[str, Any]],
    values_by_entity: dict[str, list],
) -> dict[str, Any]:
    info_box: dict[str, Any] = {}
    for row in values_by_entity.get(_SCENE_ENTITY_ID, []):
        definition = definitions.get(row["attribute_key"])
        if definition is None or definition["category"] != "scene":
            continue
        if definition["key"] in _INFOBOX_ATTR_KEYS:
            info_box[definition["key"]] = json.loads(row["value_json"])
    return info_box


def _relationship_badge(
    relationships: list[dict[str, Any]], player_id: str | None, entity_id: str,
) -> dict[str, Any]:
    if player_id is None:
        return {}
    for rel in relationships:
        if rel["to"] == player_id:
            return {rel["dimension"]: rel["value"]}
    return {}


class TrackerPresenter:
    """Render the spec §12.2 Tracker payload; read-only, audience-filtered."""

    def __init__(self, database: Database):
        self.database = database
        self.projection = ProjectionService(database)
        self.proposals = ProposalService(database, MutationEngine(database))

    def build(self, campaign_id: str, branch_id: str) -> dict[str, Any]:
        projection = self.projection.for_audience(
            campaign_id, branch_id, Audience.PLAYER_UI,
        )
        with self.database.connect() as connection:
            definitions = {
                row["key"]: row
                for row in connection.execute(
                    "SELECT key, label, category, value_type, display, maximum,"
                    " audiences_json FROM attribute_definitions WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchall()
                if Audience.PLAYER_UI.value in json.loads(row["audiences_json"])
            }
            value_rows = connection.execute(
                "SELECT entity_id, attribute_key, value_json"
                " FROM attribute_values WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
        values_by_entity: dict[str, list] = {}
        for row in value_rows:
            if row["attribute_key"] in definitions:
                values_by_entity.setdefault(row["entity_id"], []).append(row)

        player_id = _resolve_player_entity_id(projection["entities"])
        user_attributes: list[dict[str, Any]] = []
        if player_id is not None:
            for row in sorted(
                values_by_entity.get(player_id, []),
                key=lambda item: (definitions[item["attribute_key"]]["category"],
                                  item["attribute_key"]),
            ):
                user_attributes.append(_attribute_dict(
                    definitions[row["attribute_key"]],
                    json.loads(row["value_json"]),
                ))

        characters = []
        for entity in projection["entities"]:
            if entity["kind"] != EntityKind.CHARACTER.value:
                continue
            if entity["id"] == player_id:
                continue
            attrs = []
            for item in entity["attributes"]:
                definition = definitions.get(item["key"])
                if definition is None:
                    continue
                attrs.append(_attribute_dict(definition, item["value"]))
            details = {
                fact["fact_key"]: fact["content"]
                for fact in entity.get("facts", [])
                if fact["fact_type"] in {"identity", "appearance"}
            }
            characters.append({
                "name": entity["name"],
                "details": details,
                "relationship": _relationship_badge(
                    entity.get("relationships", []), player_id, entity["id"],
                ),
                "attributes": attrs,
            })

        rules = dict(projection["rules"])
        payload: dict[str, Any] = {
            "rules": rules,
            "state_version": projection["state_version"],
            "userStats": {
                "stats": [],
                "status": {},
                "skills": [],
                "inventory": {},
                "quests": {},
                "attributes": user_attributes,
            },
            "infoBox": _build_info_box(definitions, values_by_entity),
            "characters": characters,
            "pending_proposals": [
                {
                    "id": proposal.id,
                    "operation": proposal.operation,
                    "reason": proposal.reason,
                    "base_state_version": proposal.base_state_version,
                }
                for proposal in self.proposals.pending(campaign_id, branch_id)
            ],
        }
        if projection.get("combat") is not None:
            payload["combat"] = projection["combat"]
        return payload

    def render(self, campaign_id: str, branch_id: str) -> str:
        return (
            "```json\n"
            + json.dumps(self.build(campaign_id, branch_id), ensure_ascii=False, indent=2)
            + "\n```"
        )
```

Update existing `test_build_contains_rules_characters` to expect `state_version` key.

- [x] **Step 4: Run tests**

Run: `python -m pytest tests/backend/unit/test_presenter.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: project full Tracker payload for compat UI" \
  src/sillytavern_rpg_engine/orchestration/presenter.py \
  tests/backend/unit/test_presenter.py
```

---

### Task 6: Dynamic attributes renderer

**Files:**
- Create: `extensions/rpg-companion-compat/src/compat/attributes.js`
- Modify: `extensions/rpg-companion-compat/style.css`
- Test: `tests/rpg-companion-compat/unit/attributes.test.js`

**Interfaces:**
- Consumes: attribute dicts from `normalizeTrackerPayload`.
- Produces: `renderAttributeList(attributes: object[], { readOnly: boolean }): string`; `renderAttribute(attribute, options): string` supporting `bar`, `number`, `badge`, `text`, `list`, `progress`, unknown → read-only text.

- [x] **Step 1: Write the failing test**

```javascript
// tests/rpg-companion-compat/unit/attributes.test.js
import { describe, expect, it } from 'vitest';
import { renderAttributeList } from '../../../extensions/rpg-companion-compat/src/compat/attributes.js';

describe('renderAttributeList', () => {
    it('groups by category and renders known display types read-only', () => {
        const html = renderAttributeList([
            { key: 'alchemy', label: '炼金术', category: 'skill', type: 'number', value: 35, max: 100, display: 'bar' },
            { key: 'notes', label: '备注', category: 'lore', type: 'text', value: '谨慎', display: 'text' },
            { key: 'weird', label: '未知', category: 'misc', type: 'text', value: 'x', display: 'future-widget' },
        ], { readOnly: true });
        expect(html).toContain('炼金术');
        expect(html).toContain('rpg-attr-bar');
        expect(html).toContain('谨慎');
        expect(html).toContain('future-widget');
        expect(html).not.toContain('contenteditable="true"');
    });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/attributes.test.js`
Expected: FAIL

- [x] **Step 3: Implement attributes.js + CSS hooks**

```javascript
// extensions/rpg-companion-compat/src/compat/attributes.js

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
}

export function renderAttribute(attribute, { readOnly = true } = {}) {
    const label = escapeHtml(attribute.label ?? attribute.key);
    const display = attribute.display ?? 'text';
    const value = attribute.value;
    const readOnlyAttr = readOnly ? ' data-readonly="true"' : '';
    switch (display) {
    case 'bar':
    case 'progress': {
        const max = Number(attribute.max ?? 100) || 100;
        const num = Number(value ?? 0);
        const pct = Math.max(0, Math.min(100, Math.round((num / max) * 100)));
        return `<div class="rpg-attr rpg-attr-${display}"${readOnlyAttr}>` +
            `<div class="rpg-attr-label">${label}</div>` +
            `<div class="rpg-attr-bar"><span style="width:${pct}%"></span></div>` +
            `<div class="rpg-attr-value">${escapeHtml(num)} / ${escapeHtml(max)}</div>` +
            `</div>`;
    }
    case 'badge':
        return `<span class="rpg-attr rpg-attr-badge"${readOnlyAttr}>${label}: ${escapeHtml(value)}</span>`;
    case 'number':
        return `<div class="rpg-attr rpg-attr-number"${readOnlyAttr}>${label}: ${escapeHtml(value)}</div>`;
    case 'list': {
        const items = Array.isArray(value) ? value : [];
        const lis = items.map((item) => `<li>${escapeHtml(item)}</li>`).join('');
        return `<div class="rpg-attr rpg-attr-list"${readOnlyAttr}>` +
            `<div class="rpg-attr-label">${label}</div><ul>${lis}</ul></div>`;
    }
    case 'text':
    default:
        return `<div class="rpg-attr rpg-attr-text rpg-attr-unknown-display"${readOnlyAttr}>` +
            `<div class="rpg-attr-label">${label}</div>` +
            `<div class="rpg-attr-value">${escapeHtml(value)}</div></div>`;
    }
}

export function renderAttributeList(attributes, options = {}) {
    const grouped = new Map();
    for (const attribute of attributes ?? []) {
        const category = attribute.category ?? 'other';
        if (!grouped.has(category)) grouped.set(category, []);
        grouped.get(category).push(attribute);
    }
    return [...grouped.entries()].map(([category, items]) => {
        const body = items.map((item) => renderAttribute(item, options)).join('');
        return `<section class="rpg-attr-category" data-category="${escapeHtml(category)}">${body}</section>`;
    }).join('');
}
```

Append to `style.css`:

```css
.rpg-attr-category { margin-bottom: 0.75rem; }
.rpg-attr-label { font-size: 0.85rem; opacity: 0.85; }
.rpg-attr-bar { background: rgba(255,255,255,0.08); height: 8px; border-radius: 4px; overflow: hidden; }
.rpg-attr-bar > span { display: block; height: 100%; background: var(--rpg-highlight, #e94560); }
.rpg-compat-stale { background: #5c2b2b; color: #fff; padding: 0.5rem; border-radius: 4px; margin-bottom: 0.5rem; }
```

- [x] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/attributes.test.js`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add dynamic read-only attribute renderer" \
  extensions/rpg-companion-compat/src/compat/attributes.js \
  extensions/rpg-companion-compat/style.css \
  tests/rpg-companion-compat/unit/attributes.test.js
```

---

### Task 7: Read-only authoritative mode guards

**Files:**
- Create: `extensions/rpg-companion-compat/src/compat/readOnly.js`
- Modify: `extensions/rpg-companion-compat/src/systems/rendering/userStats.js`
- Modify: `extensions/rpg-companion-compat/src/systems/rendering/infoBox.js`
- Modify: `extensions/rpg-companion-compat/src/systems/rendering/thoughts.js`
- Test: `tests/rpg-companion-compat/unit/readOnly.test.js`

**Interfaces:**
- Consumes: `extensionSettings.compatMode` (added in Task 9).
- Produces: `isCompatReadOnly(): boolean`; `guardReadOnly(container: HTMLElement): void` removes `contenteditable`, blocks click-to-edit handlers via `data-rpg-readonly`.

- [x] **Step 1: Write the failing test**

```javascript
// tests/rpg-companion-compat/unit/readOnly.test.js
import { beforeEach, describe, expect, it } from 'vitest';
import { guardReadOnly, isCompatReadOnly } from '../../../extensions/rpg-companion-compat/src/compat/readOnly.js';
import { extensionSettings } from '../../../extensions/rpg-companion-compat/src/core/state.js';

describe('readOnly', () => {
    beforeEach(() => {
        document.body.innerHTML = '<div id="panel"><span contenteditable="true" class="editable">HP</span></div>';
        extensionSettings.compatMode = true;
    });

    it('detects compat read-only mode', () => {
        expect(isCompatReadOnly()).toBe(true);
        extensionSettings.compatMode = false;
        expect(isCompatReadOnly()).toBe(false);
    });

    it('strips contenteditable and marks container read-only', () => {
        const panel = document.getElementById('panel');
        guardReadOnly(panel);
        expect(panel.dataset.rpgReadonly).toBe('true');
        expect(panel.querySelector('[contenteditable]')).toBeNull();
    });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/readOnly.test.js`
Expected: FAIL

- [x] **Step 3: Implement readOnly.js and renderer guards**

```javascript
// extensions/rpg-companion-compat/src/compat/readOnly.js
import { extensionSettings } from '../core/state.js';

export function isCompatReadOnly() {
    return extensionSettings.compatMode === true;
}

export function guardReadOnly(root) {
    if (!root || !isCompatReadOnly()) return;
    root.dataset.rpgReadonly = 'true';
    root.querySelectorAll('[contenteditable]').forEach((node) => {
        node.removeAttribute('contenteditable');
    });
    root.querySelectorAll('.editable, [data-editable="true"]').forEach((node) => {
        node.classList.remove('editable');
        node.dataset.editable = 'false';
    });
}
```

In each renderer's tail (before return), add:

```javascript
import { guardReadOnly } from '../../compat/readOnly.js';
// ...
guardReadOnly($userStatsContainer?.[0] ?? $userStatsContainer);
```

Use the actual jQuery/DOM node pattern already present in each file.

In `userStats.js`, when `lastGeneratedData.compat?.userStats?.attributes` exists, append:

```javascript
import { renderAttributeList } from '../../compat/attributes.js';
// inside renderUserStats after legacy render:
if (lastGeneratedData.compat?.userStats?.attributes?.length) {
    html += renderAttributeList(lastGeneratedData.compat.userStats.attributes, { readOnly: true });
}
```

Mirror the same `compat` attributes injection in `thoughts.js` for each character card.

- [x] **Step 4: Run tests**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/readOnly.test.js`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: enforce read-only authoritative tracker UI" \
  extensions/rpg-companion-compat/src/compat/readOnly.js \
  extensions/rpg-companion-compat/src/systems/rendering/userStats.js \
  extensions/rpg-companion-compat/src/systems/rendering/infoBox.js \
  extensions/rpg-companion-compat/src/systems/rendering/thoughts.js \
  tests/rpg-companion-compat/unit/readOnly.test.js
```

---

### Task 8: Rules metadata panel routing and settings lockdown

**Files:**
- Create: `extensions/rpg-companion-compat/src/compat/rulesPanel.js`
- Create: `extensions/rpg-companion-compat/src/compat/dndPanel.js`
- Modify: `extensions/rpg-companion-compat/src/core/state.js`
- Modify: `extensions/rpg-companion-compat/settings.html`
- Test: `tests/rpg-companion-compat/unit/rulesPanel.test.js`

**Interfaces:**
- Consumes: `compat.rules` from parsed tracker.
- Produces: `selectRulesPanel(rules): 'narrative' | 'dnd' | 'custom'`; `renderDndCombatPanel(combat): string`; settings defaults forcing Together mode and disabling separate/external/history/autoUpdate UI.

- [x] **Step 1: Write the failing test**

```javascript
// tests/rpg-companion-compat/unit/rulesPanel.test.js
import { describe, expect, it } from 'vitest';
import { renderDndCombatPanel, selectRulesPanel } from '../../../extensions/rpg-companion-compat/src/compat/rulesPanel.js';

describe('rulesPanel', () => {
    it('selects panel by backend rules metadata', () => {
        expect(selectRulesPanel({ mode: 'narrative', enabled: false })).toBe('narrative');
        expect(selectRulesPanel({ mode: 'dnd-2024', enabled: true })).toBe('dnd');
        expect(selectRulesPanel({ mode: 'custom', enabled: true })).toBe('custom');
    });

    it('renders combat summary read-only', () => {
        const html = renderDndCombatPanel({
            round: 2,
            active_entity_id: 'goblin-1',
            order: ['player', 'goblin-1'],
        });
        expect(html).toContain('Round 2');
        expect(html).toContain('goblin-1');
        expect(html).not.toContain('contenteditable');
    });
});
```

Move `renderDndCombatPanel` export into `rulesPanel.js` (re-export from `dndPanel.js` if split).

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/rulesPanel.test.js`
Expected: FAIL

- [x] **Step 3: Implement rules panel + settings lockdown**

`rulesPanel.js`:

```javascript
export function selectRulesPanel(rules = {}) {
    if (rules.mode === 'dnd-2024' && rules.enabled) return 'dnd';
    if (rules.mode === 'custom' && rules.enabled) return 'custom';
    return 'narrative';
}

export function renderDndCombatPanel(combat) {
    if (!combat) return '';
    const round = combat.round ?? '?';
    const active = combat.active_entity_id ?? '—';
    const order = (combat.order ?? []).join(' → ');
    return `<section class="rpg-dnd-combat" data-readonly="true">` +
        `<div class="rpg-dnd-combat-round">Round ${round}</div>` +
        `<div class="rpg-dnd-combat-active">Active: ${active}</div>` +
        `<div class="rpg-dnd-combat-order">${order}</div>` +
        `</section>`;
}

export function applyRulesPanelVisibility({ rules, combat }, containers) {
    const panel = selectRulesPanel(rules);
    containers.narrative?.classList.toggle('rpg-hidden', panel !== 'narrative');
    containers.dnd?.classList.toggle('rpg-hidden', panel !== 'dnd');
    containers.custom?.classList.toggle('rpg-hidden', panel !== 'custom');
    if (containers.dnd && panel === 'dnd') {
        containers.dnd.innerHTML = renderDndCombatPanel(combat);
    }
}
```

In `state.js`, bump `settingsVersion` to `6` and add defaults:

```javascript
compatMode: true,
generationMode: 'together',
autoUpdate: false,
historyPersistence: { enabled: false, messageCount: 0, injectionPosition: 'assistant_message_end', contextPreamble: '', sendAllEnabledOnRefresh: false },
```

In `settings.html`, wrap Separate / External API / Auto Update / History Persistence controls with `class="rpg-compat-hidden"` and add note:

```html
<p class="rpg-compat-note">LangGraph compat fork uses Together mode with backend-authoritative Tracker JSON. Alternate generation modes are disabled.</p>
```

Add CSS: `.rpg-compat-hidden { display: none !important; }`

- [x] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/rulesPanel.test.js`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: route panels from rules metadata and lock settings" \
  extensions/rpg-companion-compat/src/compat/rulesPanel.js \
  extensions/rpg-companion-compat/src/compat/dndPanel.js \
  extensions/rpg-companion-compat/src/core/state.js \
  extensions/rpg-companion-compat/settings.html \
  extensions/rpg-companion-compat/style.css \
  tests/rpg-companion-compat/unit/rulesPanel.test.js
```

---

### Task 9: Pending proposals and stale-parse warnings

**Files:**
- Create: `extensions/rpg-companion-compat/src/compat/pendingProposals.js`
- Create: `extensions/rpg-companion-compat/src/compat/staleWarning.js`
- Test: `tests/rpg-companion-compat/unit/pendingProposals.test.js`

**Interfaces:**
- Consumes: `compat.pending_proposals`, `compat.state_version`, last committed version in `extensionSettings.compatStateVersion`.
- Produces: `renderPendingProposals(proposals): string`; `renderStaleWarning(reason): string`; `updateCompatStateVersion(version)`; `shouldShowStaleWarning({ parsedCompat, committedVersion, parseFailed })`.

- [x] **Step 1: Write the failing test**

```javascript
// tests/rpg-companion-compat/unit/pendingProposals.test.js
import { describe, expect, it } from 'vitest';
import { renderPendingProposals } from '../../../extensions/rpg-companion-compat/src/compat/pendingProposals.js';
import { shouldShowStaleWarning } from '../../../extensions/rpg-companion-compat/src/compat/staleWarning.js';

describe('pending + stale UI', () => {
    it('lists pending proposals with chat command hints', () => {
        const html = renderPendingProposals([
            { id: 'prop-1', reason: '剧情推断', base_state_version: 3 },
        ]);
        expect(html).toContain('prop-1');
        expect(html).toContain('确认提案 prop-1');
        expect(html).toContain('拒绝提案 prop-1');
    });

    it('flags stale display on parse failure or version regression', () => {
        expect(shouldShowStaleWarning({ parseFailed: true })).toBe(true);
        expect(shouldShowStaleWarning({
            parsedCompat: { state_version: 2 },
            committedVersion: 5,
            parseFailed: false,
        })).toBe(true);
        expect(shouldShowStaleWarning({
            parsedCompat: { state_version: 6 },
            committedVersion: 5,
            parseFailed: false,
        })).toBe(false);
    });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/pendingProposals.test.js`
Expected: FAIL

- [x] **Step 3: Implement modules**

```javascript
// pendingProposals.js
function escapeHtml(v) { return String(v).replaceAll('<', '&lt;'); }

export function renderPendingProposals(proposals = []) {
    if (!proposals.length) return '';
    const items = proposals.map((p) =>
        `<li><strong>${escapeHtml(p.id)}</strong> — ${escapeHtml(p.reason ?? '')}` +
        `<div class="rpg-proposal-hint">` +
        `确认提案 ${escapeHtml(p.id)} / 拒绝提案 ${escapeHtml(p.id)}` +
        `</div></li>`,
    ).join('');
    return `<section class="rpg-pending-proposals" data-readonly="true"><ul>${items}</ul></section>`;
}
```

```javascript
// staleWarning.js
export function shouldShowStaleWarning({ parsedCompat, committedVersion, parseFailed }) {
    if (parseFailed) return true;
    if (!parsedCompat || parsedCompat.state_version == null) return false;
    if (committedVersion == null) return false;
    return parsedCompat.state_version < committedVersion;
}

export function renderStaleWarning(reason) {
    return `<div class="rpg-compat-stale" role="status">${reason}</div>`;
}

export function updateCompatStateVersion(extensionSettings, version) {
    if (Number.isInteger(version)) {
        extensionSettings.compatStateVersion = version;
    }
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- tests/rpg-companion-compat/unit/pendingProposals.test.js`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: surface pending proposals and stale warnings" \
  extensions/rpg-companion-compat/src/compat/pendingProposals.js \
  extensions/rpg-companion-compat/src/compat/staleWarning.js \
  tests/rpg-companion-compat/unit/pendingProposals.test.js
```

---

### Task 10: Compat bootstrap and disable dual-write paths

**Files:**
- Create: `extensions/rpg-companion-compat/src/compat/bootstrap.js`
- Modify: `extensions/rpg-companion-compat/index.js`
- Modify: `extensions/rpg-companion-compat/src/systems/generation/injector.js`
- Modify: `extensions/rpg-companion-compat/src/systems/generation/apiClient.js`
- Modify: `vitest.config.js`
- Test: `tests/rpg-companion-compat/integration/tracker-roundtrip.test.js`

**Interfaces:**
- Consumes: all compat modules; upstream `parseResponse`, renderers, `lastGeneratedData`.
- Produces: `initCompatMode(context)` wired from `index.js` after settings load; `applyCompatTracker(parseResult)` updates UI, pending panel, stale banner, rules panel, and swipe cache without mutating authoritative fields via edit handlers.

- [x] **Step 1: Write the failing integration test**

```javascript
// tests/rpg-companion-compat/integration/tracker-roundtrip.test.js
import { beforeEach, describe, expect, it } from 'vitest';
import { parseResponse } from '../../../extensions/rpg-companion-compat/src/systems/generation/parser.js';
import { applyCompatTracker } from '../../../extensions/rpg-companion-compat/src/compat/bootstrap.js';
import { extensionSettings, lastGeneratedData } from '../../../extensions/rpg-companion-compat/src/core/state.js';

const FIXTURE = `叙述。\n\`\`\`json\n${JSON.stringify({
    rules: { mode: 'narrative', enabled: false, version: null },
    userStats: { stats: [], status: {}, skills: [], inventory: {}, quests: {}, attributes: [
        { key: 'alchemy', label: '炼金术', category: 'skill', type: 'number', value: 35, max: 100, display: 'bar' },
    ] },
    infoBox: { location: '银月城' },
    characters: [],
    pending_proposals: [],
    state_version: 4,
})}\n\`\`\``;

describe('tracker roundtrip', () => {
    beforeEach(() => {
        document.body.innerHTML = '<div id="rpg-user-stats"></div><div id="rpg-info-box"></div><div id="rpg-proposals"></div><div id="rpg-stale"></div>';
        extensionSettings.compatMode = true;
        extensionSettings.compatStateVersion = 3;
    });

    it('parses backend tracker and renders read-only compat UI', () => {
        const parsed = parseResponse(FIXTURE);
        applyCompatTracker(parsed, {
            userStatsContainer: document.getElementById('rpg-user-stats'),
            infoBoxContainer: document.getElementById('rpg-info-box'),
            proposalsContainer: document.getElementById('rpg-proposals'),
            staleContainer: document.getElementById('rpg-stale'),
        });
        expect(lastGeneratedData.compat.state_version).toBe(4);
        expect(document.getElementById('rpg-user-stats').textContent).toContain('炼金术');
        expect(document.getElementById('rpg-info-box').textContent).toContain('银月城');
        expect(extensionSettings.compatStateVersion).toBe(4);
    });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- tests/rpg-companion-compat/integration/tracker-roundtrip.test.js`
Expected: FAIL

- [x] **Step 3: Implement bootstrap + wire index/injector**

`bootstrap.js`:

```javascript
import { renderAttributeList } from './attributes.js';
import { applyRulesPanelVisibility } from './rulesPanel.js';
import { renderPendingProposals } from './pendingProposals.js';
import {
    renderStaleWarning, shouldShowStaleWarning, updateCompatStateVersion,
} from './staleWarning.js';
import { guardReadOnly } from './readOnly.js';
import {
    extensionSettings, lastGeneratedData, setLastGeneratedData,
} from '../core/state.js';

export function applyCompatTracker(parseResult, containers) {
    const compat = parseResult.compat ?? null;
    setLastGeneratedData({ ...lastGeneratedData, compat });

    if (containers.staleContainer) {
        const stale = shouldShowStaleWarning({
            parsedCompat: compat,
            committedVersion: extensionSettings.compatStateVersion,
            parseFailed: compat == null && /```json/.test(parseResult.rawText ?? ''),
        });
        containers.staleContainer.innerHTML = stale
            ? renderStaleWarning('Tracker 解析失败或已过期；显示可能落后。后端权威状态未改变。')
            : '';
    }

    if (!compat) return;

    if (containers.userStatsContainer) {
        containers.userStatsContainer.innerHTML = renderAttributeList(
            compat.userStats?.attributes ?? [], { readOnly: true },
        );
        guardReadOnly(containers.userStatsContainer);
    }
    if (containers.infoBoxContainer) {
        const box = compat.infoBox ?? {};
        containers.infoBoxContainer.innerHTML = Object.entries(box)
            .map(([k, v]) => `<div class="rpg-info-field" data-key="${k}">${k}: ${v}</div>`)
            .join('');
        guardReadOnly(containers.infoBoxContainer);
    }
    if (containers.proposalsContainer) {
        containers.proposalsContainer.innerHTML = renderPendingProposals(
            compat.pending_proposals ?? [],
        );
    }
    applyRulesPanelVisibility(
        { rules: compat.rules, combat: compat.combat },
        containers.rulesContainers ?? {},
    );
    updateCompatStateVersion(extensionSettings, compat.state_version);
}

export function initCompatMode() {
    extensionSettings.compatMode = true;
    extensionSettings.generationMode = 'together';
    extensionSettings.autoUpdate = false;
    if (extensionSettings.historyPersistence) {
        extensionSettings.historyPersistence.enabled = false;
    }
}
```

In `index.js`, after `loadSettings()`:

```javascript
import { initCompatMode, applyCompatTracker } from './src/compat/bootstrap.js';
initCompatMode();
```

Hook MESSAGE_RECEIVED handler: after `parseResponse`, call `applyCompatTracker` with panel containers.

In `injector.js` / `apiClient.js`, early-return when `extensionSettings.compatMode` for separate/external generation paths:

```javascript
if (extensionSettings.compatMode) {
    return; // authoritative tracker arrives in Together main response
}
```

Update `vitest.config.js`:

```javascript
include: [
    'tests/{unit,integration}/**/*.test.js',
    'tests/rpg-companion-compat/**/*.test.js',
],
```

- [x] **Step 4: Run tests**

Run: `npm run test:run -- tests/rpg-companion-compat`
Expected: PASS

- [x] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: bootstrap compat tracker rendering pipeline" \
  extensions/rpg-companion-compat/src/compat/bootstrap.js \
  extensions/rpg-companion-compat/index.js \
  extensions/rpg-companion-compat/src/systems/generation/injector.js \
  extensions/rpg-companion-compat/src/systems/generation/apiClient.js \
  vitest.config.js \
  tests/rpg-companion-compat/integration/tracker-roundtrip.test.js
```

---

### Task 11: README and SillyTavern wiring documentation

**Files:**
- Modify: `README.md`
- Create: `extensions/rpg-companion-compat/README.md`

**Interfaces:**
- Consumes: completed compat extension and Phase 4 server docs.
- Produces: install instructions (Git URL to `extensions/rpg-companion-compat`), Custom OpenAI body `{"campaign_id":"..."}`, explicit "do not enable DualModel Engine" warning, AGPL source-offer note.

- [x] **Step 1: Add compat README**

`extensions/rpg-companion-compat/README.md` must include:

```markdown
# RPG Companion Compat (LangGraph)

Install via SillyTavern Extensions → Install from URL:

`https://github.com/<org>/SillyTavern-DualModel-Engine/tree/main/extensions/rpg-companion-compat`

## SillyTavern setup

1. Disable the legacy DualModel Engine extension.
2. Chat Completion → Custom OpenAI-compatible:
   - Base URL: `http://127.0.0.1:8000/v1`
   - Custom Body: `{"campaign_id":"campaign-001"}`
3. Enable this extension. Generation mode is locked to Together.
4. Start backend: `python -m sillytavern_rpg_engine serve`

## Authoritative state

Tracker values are read-only. Approve inferred changes in chat:

- `确认提案 <id>`
- `拒绝提案 <id>`

## License

AGPL-3.0. See LICENSE and THIRD_PARTY_NOTICES.md.
```

- [x] **Step 2: Update root README**

Add section **"RPG Companion Compat (Phase 5)"** linking to the compat README and reiterating spec §2.2 (never run with DualModel Engine).

- [x] **Step 3: Verify docs references**

Run: `rg "DualModel Engine" README.md extensions/rpg-companion-compat/README.md`
Expected: both mention mutual exclusion.

- [x] **Step 4: Commit**

```bash
bash .harness/scripts/committer "docs: add RPG Companion compat install guide" \
  README.md extensions/rpg-companion-compat/README.md
```

---

### Task 12: Phase 5 backend + frontend integration test

**Files:**
- Create: `tests/backend/integration/test_phase5_tracker_compat.py`
- Reuse: `tests/rpg-companion-compat/integration/tracker-roundtrip.test.js`

**Interfaces:**
- Consumes: Phase 4 `create_app`, `TrackerPresenter`, compat parser via subprocess or checked-in fixture file.
- Produces: end-to-end proof that API chat response Tracker JSON parses through compat schema and contains `state_version`, `rules`, dynamic `attributes`.

- [x] **Step 1: Write the failing Python integration test**

```python
# tests/backend/integration/test_phase5_tracker_compat.py
import json
import re

from fastapi.testclient import TestClient

from sillytavern_rpg_engine.llm.scripted import ScriptedLLMClient
from sillytavern_rpg_engine.server.app import create_app
from sillytavern_rpg_engine.services.attributes import (
    CreateEntityOperation, DefineAttributeOperation, SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import EntityAttributeService
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition, AttributeType, Audience, DisplayType, EntityKind,
)


def _world(database):
    ids = iter(f"evt-{i}" for i in range(200))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-14T00:00:00Z")
    campaigns.create_campaign("c1", "Compat")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="erin", kind=EntityKind.CHARACTER, name="艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金术", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
        ),
    ))
    attrs.apply_explicit("c1", "main", 2, SetAttributeOperation(
        "erin", "alchemy", 35, None,
    ))
    return campaigns


def test_chat_tracker_json_is_compat_shaped(database, tmp_path):
    _world(database)
    narrator = ScriptedLLMClient()
    narrator.queue("艾琳检查炼金设备。")
    client = TestClient(create_app(database=database, narrator=narrator))
    response = client.post("/v1/chat/completions", json={
        "campaign_id": "c1",
        "messages": [{"role": "user", "content": "查看艾琳"}],
    })
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    match = re.search(r"```json\s*\n(.*?)```", content, re.DOTALL)
    assert match, "tracker fence missing"
    tracker = json.loads(match.group(1))
    assert tracker["rules"]["mode"] == "narrative"
    assert "state_version" in tracker
    erin = next(c for c in tracker["characters"] if c["name"] == "艾琳")
    assert erin["attributes"][0]["key"] == "alchemy"
    assert erin["attributes"][0]["display"] == "bar"
```

- [x] **Step 2: Run test to verify it fails or passes**

Run: `python -m pytest tests/backend/integration/test_phase5_tracker_compat.py -q`
Expected: PASS after Task 5; FAIL before Task 5 (missing `state_version` / attributes on characters)

- [x] **Step 3: Run full verification suite**

```bash
python -m pytest tests/backend -q
npm run test:run -- tests/rpg-companion-compat
python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend
```

Expected: all PASS

- [x] **Step 4: Commit**

```bash
bash .harness/scripts/committer "test: add phase 5 tracker compat integration coverage" \
  tests/backend/integration/test_phase5_tracker_compat.py
```

---

## Self-Review Checklist

| Spec requirement | Task |
|---|---|
| §12.1 Together only; disable Separate/External/Auto Update/History Persistence | 8, 10 |
| §12.1 `rules` metadata drives panel selection | 5, 8, 10 |
| §12.2 dynamic `attributes` + legacy fields | 5, 6, 7, 10 |
| §12.3 read-only authoritative mode | 7, 10 |
| §12.3 parse failure stale warning, no backend rollback | 9, 10 |
| §12.4 narrowed JSON cleaning | 4 |
| §2.3 AGPL fork with notices | 1, 11 |
| §17.2 compat parse + dynamic render | 3, 6, 10, 12 |
| §17.4 release acceptance items 1–3, 9 | 5, 10, 11, 12 |

**Type consistency:** `normalizeTrackerPayload` → `lastGeneratedData.compat` → `applyCompatTracker` uses the same keys as `TrackerPresenter.build`. `state_version` is always an integer on successful parse.

**Explicitly deferred to Phase 6:** swipe/edit/delete branch recovery using `history_hash`, long-run simulation, real SillyTavern Playwright E2E, inventory/quests legacy panels beyond empty defaults (no schema yet).
