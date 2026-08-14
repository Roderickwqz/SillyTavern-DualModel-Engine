import { describe, expect, it } from 'vitest';
import { cacheCompatTracker, loadCompatTracker } from '../../../extensions/rpg-companion-compat/src/compat/lineageCache.js';

describe('lineageCache', () => {
    it('stores and loads compat tracker per swipe without mutating committed settings', () => {
        const message = { swipe_id: 1, extra: {} };
        const compat = { state_version: 3, rules: { mode: 'narrative' } };
        cacheCompatTracker(message, 1, compat);
        expect(loadCompatTracker(message, 1)).toEqual(compat);
        expect(loadCompatTracker(message, 0)).toBeNull();
    });
});
