import Ajv from 'ajv';
import patchSchema from '../schemas/patch.schema.json';

function pathsOverlap(left, right) {
    return left === right || left.startsWith(`${right}/`) || right.startsWith(`${left}/`);
}

function copyErrors(errors) {
    return structuredClone(errors);
}

function normalizePolicy(policy) {
    const errors = [];
    const source = policy && typeof policy === 'object' && !Array.isArray(policy) ? policy : {};
    if (source !== policy) errors.push({ message: 'Invalid policy' });

    function normalizePaths(paths, label) {
        if (paths === undefined) return [];
        if (!Array.isArray(paths)) {
            errors.push({ message: `Invalid ${label} paths` });
            return [];
        }

        return paths.filter((path) => {
            if (typeof path === 'string') return true;
            errors.push({ message: `Invalid ${label} path` });
            return false;
        });
    }

    return {
        expectedVersion: source.expectedVersion,
        allowedPaths: normalizePaths(source.allowedPaths, 'allowed'),
        lockedPaths: normalizePaths(source.lockedPaths, 'locked'),
        errors,
    };
}

export function createStateValidator({ presets }) {
    const ajv = new Ajv({ allErrors: true, strict: false });
    const patchValidator = ajv.compile(patchSchema);
    const presetById = new Map(presets.map((preset) => [preset.id, preset]));
    const stateValidators = new Map(presets.map((preset) => [preset.id, ajv.compile(preset.stateSchema)]));

    return {
        validateState(presetId, state) {
            const validate = stateValidators.get(presetId);
            const schemaOk = Boolean(validate?.(state));
            const schemaErrors = schemaOk
                ? []
                : copyErrors(validate?.errors ?? [{ message: 'Unknown preset' }]);
            const invariantErrors = schemaOk
                ? copyErrors(presetById.get(presetId)?.validateInvariants?.(state) ?? [])
                : [];

            return { ok: schemaOk && invariantErrors.length === 0, errors: [...schemaErrors, ...invariantErrors] };
        },

        validatePatch(presetId, patch, policy = {}) {
            const schemaOk = Boolean(patchValidator(patch));
            const normalizedPolicy = normalizePolicy(policy);
            const policyErrors = normalizedPolicy.errors;
            const { allowedPaths, lockedPaths } = normalizedPolicy;

            if (!presetById.has(presetId)) policyErrors.push({ message: 'Unknown preset' });
            if (patch?.base_version !== normalizedPolicy.expectedVersion) policyErrors.push({ message: 'base_version mismatch' });

            for (const operation of Array.isArray(patch?.operations) ? patch.operations : []) {
                if (!operation || typeof operation.path !== 'string') continue;
                if (!allowedPaths.some((path) => operation.path === path || operation.path.startsWith(`${path}/`))) {
                    policyErrors.push({ message: `Path not allowed: ${operation.path}` });
                }
                if (lockedPaths.some((path) => pathsOverlap(operation.path, path))) {
                    policyErrors.push({ message: `Path locked: ${operation.path}` });
                }
            }

            return { ok: schemaOk && policyErrors.length === 0, errors: [...copyErrors(patchValidator.errors ?? []), ...policyErrors] };
        },
    };
}
