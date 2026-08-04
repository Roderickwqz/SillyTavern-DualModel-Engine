function touches(operation, path) {
    return operation.path === path || operation.path.startsWith(`${path}/`) || path.startsWith(`${operation.path}/`);
}

export function createStateTab({ validateState, diffState, confirm, commitManualPatch }) {
    return {
        parse(text) { try { return { ok: true, value: JSON.parse(text) }; } catch (error) { return { ok: false, reason: 'invalid-json', error }; } },
        preview(before, after) { return diffState(before, after); },
        async saveStateEdit(before, after, lockedPaths = [], allowedPaths = null) {
            if (after?.version !== before?.version) return { ok: false, reason: 'system-locked-version' };
            const validation = validateState(after);
            if (!validation?.ok) return { ok: false, reason: 'invalid-state', errors: validation?.errors ?? [] };
            const operations = diffState(before, after);
            if (!operations.length) return { ok: true, unchanged: true, operations };
            if (allowedPaths && operations.some(operation => !allowedPaths.some(path => operation.path === path || operation.path.startsWith(`${path}/`)))) return { ok: false, reason: 'path-not-allowed', operations };
            const touchedLocked = lockedPaths.filter(path => operations.some(operation => touches(operation, path)));
            if (touchedLocked.length && !await confirm({ action: 'edit-locked-state', lockedPaths: touchedLocked, operations })) return { ok: false, reason: 'cancelled' };
            return commitManualPatch({ baseVersion: before.version, operations, nextState: after, source: 'user-editor' });
        },
    };
}
