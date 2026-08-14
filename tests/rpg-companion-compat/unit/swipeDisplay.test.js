import { beforeEach, describe, expect, it } from 'vitest';
import { applyCompatTracker } from '../../../extensions/rpg-companion-compat/src/compat/bootstrap.js';
import { restoreSwipeDisplay, bindSwipeDisplay } from '../../../extensions/rpg-companion-compat/src/compat/swipeDisplay.js';
import { cacheCompatTracker } from '../../../extensions/rpg-companion-compat/src/compat/lineageCache.js';
import { extensionSettings, lastGeneratedData } from '../../../extensions/rpg-companion-compat/src/core/state.js';

const COMPAT = {
    state_version: 4,
    rules: { mode: 'narrative' },
    userStats: { attributes: [{ key: 'str', label: 'STR', display: 'number', value: 10 }] },
    infoBox: { location: '银月城' },
    pending_proposals: [],
};

function makeContainers() {
    return {
        userStatsContainer: document.getElementById('rpg-user-stats'),
        infoBoxContainer: document.getElementById('rpg-info-box'),
        proposalsContainer: document.getElementById('rpg-proposals'),
        staleContainer: document.getElementById('rpg-stale'),
        rulesContainers: {},
    };
}

describe('swipeDisplay', () => {
    beforeEach(() => {
        document.body.innerHTML = [
            '<div id="rpg-user-stats"></div>',
            '<div id="rpg-info-box"></div>',
            '<div id="rpg-proposals"></div>',
            '<div id="rpg-stale"></div>',
        ].join('');
        extensionSettings.compatMode = true;
        delete extensionSettings.compatStateVersion;
    });

    it('restores cached display without writing authoritative state', () => {
        const message = { swipe_id: 2, extra: {} };
        cacheCompatTracker(message, COMPAT, 2);
        const result = restoreSwipeDisplay(message, makeContainers());
        expect(result).toBe(true);
        expect(document.getElementById('rpg-user-stats').textContent).toContain('STR');
        expect(document.getElementById('rpg-info-box').textContent).toContain('银月城');
        expect(lastGeneratedData.compat).toBeUndefined();
        expect(extensionSettings.compatStateVersion).toBeUndefined();
    });

    it('skips stale banner and re-caching on restore', () => {
        const message = { swipe_id: 2, extra: {} };
        cacheCompatTracker(message, { ...COMPAT, state_version: 1 }, 2);
        restoreSwipeDisplay(message, makeContainers());
        expect(document.getElementById('rpg-stale').textContent).toBe('');
        expect(message.extra.rpg_compat_swipes['2']).toEqual({ ...COMPAT, state_version: 1 });
    });

    it('returns false without cache and writes nothing', () => {
        const message = { swipe_id: 1 };
        const result = restoreSwipeDisplay(message, makeContainers());
        expect(result).toBe(false);
        expect(document.getElementById('rpg-user-stats').textContent).toBe('');
        expect(document.getElementById('rpg-info-box').textContent).toBe('');
        expect(document.getElementById('rpg-stale').textContent).toBe('');
        expect(lastGeneratedData.compat).toBeUndefined();
        expect(extensionSettings.compatStateVersion).toBeUndefined();
    });

    it('bindSwipeDisplay rerenders from cache and skips user messages', () => {
        const listeners = {};
        const eventSource = { on: (type, fn) => { listeners[type] = fn; } };
        const eventTypes = { MESSAGE_SWIPED: 'swiped', MESSAGE_UPDATED: 'updated' };
        const chat = [
            { swipe_id: 2, extra: {}, is_user: false },
            { swipe_id: 0, is_user: true },
        ];
        cacheCompatTracker(chat[0], COMPAT, 2);
        bindSwipeDisplay(eventSource, eventTypes, () => ({ chat }), makeContainers());
        listeners.swiped(1);
        expect(document.getElementById('rpg-user-stats').textContent).toBe('');
        listeners.swiped(0);
        expect(document.getElementById('rpg-user-stats').textContent).toContain('STR');
        expect(document.getElementById('rpg-info-box').textContent).toContain('银月城');
        listeners.updated(0);
        expect(document.getElementById('rpg-user-stats').textContent).toContain('STR');
    });

    it('positive control: non-skip applyCompatTracker still writes authoritative state', () => {
        applyCompatTracker(
            { compat: { state_version: 7, rules: {} }, rawText: '' },
            makeContainers(),
            { swipe_id: 1, extra: {} },
        );
        expect(lastGeneratedData.compat.state_version).toBe(7);
        expect(extensionSettings.compatStateVersion).toBe(7);
    });
});
