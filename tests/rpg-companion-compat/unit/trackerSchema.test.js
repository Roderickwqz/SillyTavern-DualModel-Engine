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
