const STORE_KEY = 'rpg_compat_swipes';

function cloneSerializable(value) {
    try {
        return structuredClone(value);
    } catch {
        return JSON.parse(JSON.stringify(value));
    }
}

export function cacheCompatTracker(message, compat, swipeId = message?.swipe_id ?? 0) {
    if (!message || compat == null) return;
    if (!message.extra || typeof message.extra !== 'object') message.extra = {};
    if (!message.extra[STORE_KEY] || typeof message.extra[STORE_KEY] !== 'object') {
        message.extra[STORE_KEY] = {};
    }
    message.extra[STORE_KEY][String(swipeId)] = cloneSerializable(compat);
}

export function loadCompatTracker(message, swipeId = message?.swipe_id ?? 0) {
    const store = message?.extra?.[STORE_KEY];
    if (!store) return null;
    return store[String(swipeId)] ?? null;
}
