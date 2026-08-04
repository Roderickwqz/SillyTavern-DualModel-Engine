const BLOCKED_KEYS = new Set(['__proto__', 'prototype', 'constructor']);

function errorMessage(error) {
    return error instanceof Error ? error.message : String(error);
}

export function decodePointer(path) {
    if (path === '') return [];
    if (typeof path !== 'string' || !path.startsWith('/')) throw new Error(`Invalid JSON pointer: ${String(path)}`);

    const encoded = path.slice(1).split('/');
    if (encoded.some((part) => /~(?:[^01]|$)/.test(part))) throw new Error(`Invalid JSON pointer escape: ${path}`);

    const parts = encoded.map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'));
    if (parts.some((part) => BLOCKED_KEYS.has(part))) throw new Error(`Blocked JSON pointer: ${path}`);
    return parts;
}

function parentAt(root, parts) {
    let parent = root;
    for (const part of parts.slice(0, -1)) {
        if (parent === null || typeof parent !== 'object' || !Object.hasOwn(parent, part)) {
            throw new Error(`Missing path segment: ${part}`);
        }
        parent = parent[part];
    }
    return { parent, key: parts.at(-1) };
}

function pathsOverlap(left, right) {
    return left === right || left.startsWith(`${right}/`) || right.startsWith(`${left}/`);
}

function isArrayIndex(key) {
    return /^(0|[1-9]\d*)$/.test(key);
}

function assertInput(patch, policy, validateState) {
    if (!patch || typeof patch !== 'object' || !Array.isArray(patch.operations)) throw new Error('Invalid patch');
    if (!policy || typeof policy !== 'object' || !Array.isArray(policy.allowedPaths) || !Array.isArray(policy.lockedPaths)) {
        throw new Error('Invalid policy');
    }
    if (![...policy.allowedPaths, ...policy.lockedPaths].every((path) => typeof path === 'string')) throw new Error('Invalid policy path');
    if (typeof validateState !== 'function') throw new Error('Invalid state validator');
}

export function applyValidatedPatch({ state, patch, policy, validateState }) {
    try {
        assertInput(patch, policy, validateState);
        const value = structuredClone(state);
        for (const operation of patch.operations) {
            if (!operation || typeof operation !== 'object' || typeof operation.op !== 'string' || typeof operation.path !== 'string') {
                throw new Error('Invalid patch operation');
            }
            const allowed = policy.allowedPaths.some((path) => path === '/' || operation.path === path || operation.path.startsWith(`${path}/`));
            const locked = policy.lockedPaths.some((path) => pathsOverlap(operation.path, path));
            if (!allowed || locked) throw new Error(`Rejected path: ${operation.path}`);

            const parts = decodePointer(operation.path);
            if (!parts.length) throw new Error('Root replacement is not supported');
            const { parent, key } = parentAt(value, parts);
            if (operation.op === 'add') {
                if (Array.isArray(parent)) {
                    if (key === '-') parent.push(structuredClone(operation.value));
                    else if (!isArrayIndex(key) || Number(key) > parent.length) throw new Error(`Invalid array add index: ${operation.path}`);
                    else parent.splice(Number(key), 0, structuredClone(operation.value));
                } else {
                    if (parent === null || typeof parent !== 'object') throw new Error(`Invalid add parent: ${operation.path}`);
                    parent[key] = structuredClone(operation.value);
                }
                continue;
            }
            if (operation.op === 'replace') {
                if (Array.isArray(parent) && (!isArrayIndex(key) || Number(key) >= parent.length)) {
                    throw new Error(`Invalid array replace index: ${operation.path}`);
                }
                if (parent === null || typeof parent !== 'object' || !Object.hasOwn(parent, key)) throw new Error(`Replace target missing: ${operation.path}`);
                parent[key] = structuredClone(operation.value);
                continue;
            }
            if (operation.op === 'remove') {
                if (Array.isArray(parent) && (!isArrayIndex(key) || Number(key) >= parent.length)) {
                    throw new Error(`Invalid array remove index: ${operation.path}`);
                }
                if (parent === null || typeof parent !== 'object' || !Object.hasOwn(parent, key)) throw new Error(`Remove target missing: ${operation.path}`);
                if (Array.isArray(parent)) parent.splice(Number(key), 1);
                else delete parent[key];
                continue;
            }
            throw new Error(`Unsupported operation: ${operation.op}`);
        }
        const validation = validateState(value);
        return validation.ok ? { ok: true, value, errors: [] } : { ok: false, errors: structuredClone(validation.errors ?? []) };
    } catch (error) {
        return { ok: false, errors: [{ message: errorMessage(error) }] };
    }
}
