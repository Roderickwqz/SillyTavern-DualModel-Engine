import { loadCompatTracker } from './lineageCache.js';
import { applyCompatTracker } from './bootstrap.js';
import { extensionSettings } from '../core/state.js';

export function restoreSwipeDisplay(message, containers) {
    const compat = loadCompatTracker(message);
    if (!compat) return false;
    applyCompatTracker({ compat, rawText: '' }, containers, message, { skipStateSync: true });
    return true;
}

export function bindSwipeDisplay(eventSource, eventTypes, getContext, containers) {
    const rerender = (messageIndex) => {
        if (!extensionSettings.enabled) return;
        const message = getContext()?.chat?.[messageIndex];
        if (!message || message.is_user) return;
        restoreSwipeDisplay(message, containers);
    };
    eventSource.on(eventTypes.MESSAGE_SWIPED, rerender);
    eventSource.on(eventTypes.MESSAGE_UPDATED, rerender);
}
