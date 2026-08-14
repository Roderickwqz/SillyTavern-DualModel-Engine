/** Root keys that identify a LangGraph Tracker payload (spec §12.2 + extensions). */
export const TRACKER_ROOT_KEYS = Object.freeze([
    'rules',
    'userStats',
    'infoBox',
    'characters',
    'pending_proposals',
    'state_version',
    'combat',
]);

/** Keys used together with userStats/infoBox to detect tracker JSON blocks. */
export const TRACKER_SIGNATURE_KEYS = Object.freeze(['userStats', 'infoBox']);

const DEFAULT_RULES = Object.freeze({
    mode: 'narrative',
    enabled: false,
    version: null,
});

const DEFAULT_USER_STATS = Object.freeze({
    stats: [],
    status: {},
    skills: [],
    inventory: {},
    quests: {},
    attributes: [],
});

/**
 * @param {unknown} value
 * @returns {value is Record<string, unknown>}
 */
export function isTrackerPayload(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return false;
    }
    return TRACKER_SIGNATURE_KEYS.some((key) => key in value);
}

/**
 * @param {Record<string, unknown>} raw
 */
export function normalizeTrackerPayload(raw) {
    const rules = { ...DEFAULT_RULES, ...(raw.rules ?? {}) };
    const userStats = {
        ...DEFAULT_USER_STATS,
        ...(raw.userStats ?? {}),
        attributes: Array.isArray(raw.userStats?.attributes)
            ? raw.userStats.attributes
            : [],
    };
    const infoBox = { ...(raw.infoBox ?? {}) };
    const characters = Array.isArray(raw.characters) ? raw.characters : [];
    const pending_proposals = Array.isArray(raw.pending_proposals)
        ? raw.pending_proposals
        : [];
    const state_version = Number.isInteger(raw.state_version)
        ? raw.state_version
        : null;
    const combat = raw.combat ?? null;
    return {
        rules,
        userStats,
        infoBox,
        characters,
        pending_proposals,
        state_version,
        combat,
    };
}
