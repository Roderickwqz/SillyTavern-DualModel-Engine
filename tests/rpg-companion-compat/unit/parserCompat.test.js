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
