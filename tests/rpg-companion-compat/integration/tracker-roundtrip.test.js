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
