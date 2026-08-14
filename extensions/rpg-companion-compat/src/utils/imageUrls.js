/**
 * Image URL Utilities Module
 * Centralizes validation for image sources captured from DOM or settings.
 */

const DEFAULT_IMAGE_BASE_URL = typeof window !== 'undefined'
    ? window.location.href
    : 'http://localhost/';

export function normalizeImageSrc(src) {
    return String(src ?? '').trim();
}

export function resolveImageUrl(src, baseUrl = DEFAULT_IMAGE_BASE_URL) {
    const normalized = normalizeImageSrc(src);
    if (!normalized) {
        return null;
    }

    try {
        return new URL(normalized, baseUrl);
    } catch {
        return null;
    }
}

export function isSafeImageSrc(src) {
    const normalized = normalizeImageSrc(src);
    if (!normalized) {
        return false;
    }

    const candidate = resolveImageUrl(normalized);
    if (!candidate) {
        return false;
    }

    const protocol = candidate.protocol.toLowerCase();
    if (protocol === 'http:' || protocol === 'https:' || protocol === 'blob:') {
        return true;
    }

    if (protocol === 'data:') {
        return normalized.toLowerCase().startsWith('data:image/');
    }

    return false;
}

export function getSafeImageSrc(src) {
    const normalized = normalizeImageSrc(src);
    return isSafeImageSrc(normalized) ? normalized : null;
}
