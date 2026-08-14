import { describe, expect, it } from 'vitest';
import { cacheCompatTracker, loadCompatTracker } from '../../../extensions/rpg-companion-compat/src/compat/lineageCache.js';

describe('lineageCache', () => {
    it('stores and loads compat tracker per swipe without mutating committed settings', () => {
        const message = { swipe_id: 1, extra: {} };
        const compat = { state_version: 3, rules: { mode: 'narrative' } };
        cacheCompatTracker(message, compat, 1);
        expect(loadCompatTracker(message, 1)).toEqual(compat);
        expect(loadCompatTracker(message, 0)).toBeNull();
    });

    it('loads by default swipe_id when not passed explicitly', () => {
        const message = { swipe_id: 3 };
        cacheCompatTracker(message, { state_version: 1 }, 3);
        expect(loadCompatTracker(message)).toEqual({ state_version: 1 });
    });

    it('caches by default swipe_id when not passed explicitly', () => {
        const message = { swipe_id: 4 };
        cacheCompatTracker(message, { state_version: 1 });
        expect(loadCompatTracker(message)).toEqual({ state_version: 1 });
    });

    it('overwrites cache entry when same swipe id is re-cached', () => {
        const message = { swipe_id: 2, extra: {} };
        cacheCompatTracker(message, { state_version: 1 }, 2);
        cacheCompatTracker(message, { state_version: 5 }, 2);
        expect(loadCompatTracker(message, 2)).toEqual({ state_version: 5 });
    });

    it('ignores null compat without creating the extra store', () => {
        const message = { swipe_id: 1 };
        cacheCompatTracker(message, null, 1);
        expect(message.extra).toBeUndefined();
        expect(loadCompatTracker(message)).toBeNull();
    });

    it('returns null when message is undefined', () => {
        expect(loadCompatTracker(undefined)).toBeNull();
    });

    it('clones stored compat so later mutation of the source is isolated', () => {
        const compat = { state_version: 2, rules: { mode: 'narrative' } };
        const message = { swipe_id: 1, extra: {} };
        cacheCompatTracker(message, compat, 1);
        compat.rules.mode = 'dnd';
        compat.state_version = 9;
        expect(loadCompatTracker(message, 1)).toEqual({ state_version: 2, rules: { mode: 'narrative' } });
    });
});
