import { DATA_SCHEMA_VERSION } from './constants.js';

export function createEmptyEnvelope({ presetId, initialState }) {
    const snapshot = structuredClone(initialState);
    if (snapshot.version !== 0) throw new Error('Initial state version must be 0');

    return {
        schemaVersion: DATA_SCHEMA_VERSION,
        stateVersion: 0,
        headRevision: 0,
        preset: { id: presetId, version: 1 },
        initialSnapshot: structuredClone(snapshot),
        activeSnapshot: snapshot,
        activeRef: null,
        configOverrides: {},
        taskStatus: { state: 'idle', requestId: null },
        lastCommittedRequestId: null,
    };
}

export function migrateEnvelope(input) {
    const copy = structuredClone(input);
    if (copy.schemaVersion === DATA_SCHEMA_VERSION) return { ok: true, value: copy };
    return { ok: false, error: new Error(`Unsupported data schema version: ${copy.schemaVersion}`) };
}
