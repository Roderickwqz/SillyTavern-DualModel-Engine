import { describe, expect, it } from 'vitest';
import validState from '../fixtures/narrative-state.json';
import { narrativePreset } from '../../src/rules/narrative.js';
import { createStateValidator } from '../../src/state-validator.js';

const validator = createStateValidator({ presets: [narrativePreset] });

describe('state validator', () => {
    it('accepts the valid narrative fixture', () => {
        expect(validator.validateState('narrative', validState)).toEqual({ ok: true, errors: [] });
    });

    it('rejects an out-of-range relationship and a locked Patch path', () => {
        const stateResult = validator.validateState('narrative', {
            ...validState,
            characters: { Mira: { attitude: 'calm', trust: 101, injuries: [] } },
        });
        const patchResult = validator.validatePatch('narrative', {
            base_version: 1,
            operations: [{ op: 'replace', path: '/characters/Mira/trust', value: 20, reason: 'manual' }],
        }, { expectedVersion: 1, allowedPaths: ['/characters'], lockedPaths: ['/characters/Mira/trust'] });

        expect(stateResult.ok).toBe(false);
        expect(patchResult.ok).toBe(false);
        expect(patchResult.errors).toContainEqual({ message: 'Path locked: /characters/Mira/trust' });
    });

    it('reports an unknown preset without throwing or changing its input', () => {
        const state = structuredClone(validState);

        expect(validator.validateState('missing', state)).toEqual({
            ok: false,
            errors: [{ message: 'Unknown preset' }],
        });
        expect(state).toEqual(validState);
    });

    it('rejects a Patch with a mismatched base version', () => {
        const result = validator.validatePatch('narrative', {
            base_version: 2,
            operations: [],
        }, { expectedVersion: 1, allowedPaths: ['/scene'], lockedPaths: [] });

        expect(result.ok).toBe(false);
        expect(result.errors).toContainEqual({ message: 'base_version mismatch' });
    });

    it('uses JSON Pointer boundaries when checking allowed paths', () => {
        const result = validator.validatePatch('narrative', {
            base_version: 1,
            operations: [{ op: 'replace', path: '/charactersX/Mira', value: {}, reason: 'typo' }],
        }, { expectedVersion: 1, allowedPaths: ['/characters'], lockedPaths: [] });

        expect(result.ok).toBe(false);
        expect(result.errors).toContainEqual({ message: 'Path not allowed: /charactersX/Mira' });
    });

    it('rejects locked parent and child path overlaps', () => {
        const result = validator.validatePatch('narrative', {
            base_version: 1,
            operations: [
                { op: 'replace', path: '/characters/Mira', value: {}, reason: 'parent' },
                { op: 'replace', path: '/scene/location', value: 'inn', reason: 'child' },
            ],
        }, {
            expectedVersion: 1,
            allowedPaths: ['/characters', '/scene'],
            lockedPaths: ['/characters/Mira/trust', '/scene'],
        });

        expect(result.ok).toBe(false);
        expect(result.errors).toContainEqual({ message: 'Path locked: /characters/Mira' });
        expect(result.errors).toContainEqual({ message: 'Path locked: /scene/location' });
    });

    it('returns schema errors for malformed and null patches without throwing', () => {
        expect(() => validator.validatePatch('narrative', null, {
            expectedVersion: 1, allowedPaths: [], lockedPaths: [],
        })).not.toThrow();
        expect(validator.validatePatch('narrative', null, {
            expectedVersion: 1, allowedPaths: [], lockedPaths: [],
        }).ok).toBe(false);
    });

    it('returns a policy error instead of throwing for a null policy', () => {
        const patch = { base_version: 1, operations: [] };

        expect(() => validator.validatePatch('narrative', patch, null)).not.toThrow();
        expect(validator.validatePatch('narrative', patch, null)).toMatchObject({
            ok: false,
            errors: expect.arrayContaining([{ message: 'Invalid policy' }]),
        });
    });

    it('rejects non-string path policy entries without throwing', () => {
        const result = validator.validatePatch('narrative', {
            base_version: 1,
            operations: [{ op: 'replace', path: '/scene/location', value: 'inn', reason: 'move' }],
        }, {
            expectedVersion: 1,
            allowedPaths: ['/scene', Symbol('not-a-path')],
            lockedPaths: [42, Symbol('not-a-path')],
        });

        expect(result).toMatchObject({
            ok: false,
            errors: expect.arrayContaining([
                { message: 'Invalid allowed path' },
                { message: 'Invalid locked path' },
            ]),
        });
    });

    it('does not share mutable input or Ajv error results', () => {
        const invalidState = { ...validState, scene: { location: 1, time: 'night' } };
        const first = validator.validateState('narrative', invalidState);
        first.errors[0].message = 'mutated';
        const second = validator.validateState('narrative', invalidState);

        expect(invalidState.scene.location).toBe(1);
        expect(second.errors[0].message).not.toBe('mutated');
    });
});
