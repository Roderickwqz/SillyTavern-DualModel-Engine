import { loadCompatTracker } from './lineageCache.js';
import { applyCompatTracker } from './bootstrap.js';

export function restoreSwipeDisplay(message, containers) {
    const compat = loadCompatTracker(message);
    if (!compat) return false;
    applyCompatTracker({ compat, rawText: '' }, containers, message, { skipStateSync: true });
    return true;
}

export function bindSwipeDisplay(eventSource, eventTypes, getContext, containers) {
    const rerender = (messageIndex) => {
        const message = getContext()?.chat?.[messageIndex];
        if (!message || message.is_user) return;
        restoreSwipeDisplay(message, containers);
    };
    eventSource.on(eventTypes.MESSAGE_SWIPED, rerender);
    eventSource.on(eventTypes.MESSAGE_UPDATED, rerender);
}
