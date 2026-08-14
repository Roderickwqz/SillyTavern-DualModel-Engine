const STORE_KEY = 'rpg_compat_swipes';

export function cacheCompatTracker(message, swipeId, compat) {
    if (!message || compat == null) return;
    if (!message.extra || typeof message.extra !== 'object') message.extra = {};
    if (!message.extra[STORE_KEY] || typeof message.extra[STORE_KEY] !== 'object') {
        message.extra[STORE_KEY] = {};
    }
    message.extra[STORE_KEY][String(swipeId)] = structuredClone(compat);
}

export function loadCompatTracker(message, swipeId = message?.swipe_id ?? 0) {
    const store = message?.extra?.[STORE_KEY];
    if (!store) return null;
    return store[String(swipeId)] ?? null;
}
