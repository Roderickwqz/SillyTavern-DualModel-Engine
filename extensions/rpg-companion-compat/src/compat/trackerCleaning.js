import { isTrackerPayload } from './trackerSchema.js';

const JSON_FENCE = /```json\s*\n([\s\S]*?)```/gim;

export function stripTrackerJsonBlocks(text) {
    if (!text) return text;
    return text.replace(JSON_FENCE, (full, inner) => {
        try {
            const parsed = JSON.parse(inner.trim());
            return isTrackerPayload(parsed) ? '' : full;
        } catch {
            return full;
        }
    }).replace(/\n{3,}/g, '\n\n').trim();
}

/** SillyTavern regex scripts cannot call JS — use a conservative pattern for display cleaning. */
export function buildTrackerCleaningRegex() {
    // Matches fenced JSON containing userStats and/or infoBox object keys.
    return '/```json\\s*\\n[\\s\\S]*?"(?:userStats|infoBox)"\\s*:[\\s\\S]*?```/gim';
}
