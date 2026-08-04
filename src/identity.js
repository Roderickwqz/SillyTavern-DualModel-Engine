import { NAMESPACE } from './constants.js';

function namespace(extra) {
    extra[NAMESPACE] ??= {};
    return extra[NAMESPACE];
}

export function ensureMessageId(message, makeId = () => crypto.randomUUID()) {
    message.extra ??= {};
    const current = namespace(message.extra);
    current.messageId ??= makeId();
    const messageId = current.messageId;

    for (const swipe of message.swipe_info ?? []) {
        swipe.extra ??= {};
        namespace(swipe.extra).messageId = messageId;
    }
    return messageId;
}

export async function hashText(text, subtle = crypto.subtle) {
    const digest = await subtle.digest('SHA-256', new TextEncoder().encode(String(text)));
    const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    return `sha256:${hex}`;
}
