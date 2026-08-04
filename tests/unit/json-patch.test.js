import { expect, it } from 'vitest';
import { applyValidatedPatch, decodePointer } from '../../src/json-patch.js';

it('applies a replacement to a clone', () => {
    const state = { characters: { Mira: { trust: 20 } } };
    const result = applyValidatedPatch({
        state,
        patch: { operations: [{ op: 'replace', path: '/characters/Mira/trust', value: 25, reason: 'helped' }] },
        policy: { allowedPaths: ['/characters'], lockedPaths: [] },
        validateState: () => ({ ok: true, errors: [] }),
    });
    expect(result.value.characters.Mira.trust).toBe(25);
    expect(state.characters.Mira.trust).toBe(20);
});

it('rejects prototype-polluting and missing replacement paths', () => {
    const blocked = applyValidatedPatch({
        state: {},
        patch: { operations: [{ op: 'add', path: '/__proto__/polluted', value: true, reason: 'bad' }] },
        policy: { allowedPaths: ['/'], lockedPaths: [] },
        validateState: () => ({ ok: true, errors: [] }),
    });
    expect(blocked.ok).toBe(false);
    expect(Object.prototype.polluted).toBeUndefined();
});

it('adds an object key and appends to an array with dash', () => {
    const result = applyValidatedPatch({
        state: { meta: {}, items: ['a'] },
        patch: { operations: [
            { op: 'add', path: '/meta/status', value: 'ready' },
            { op: 'add', path: '/items/-', value: 'b' },
        ] },
        policy: { allowedPaths: ['/meta', '/items'], lockedPaths: [] },
        validateState: () => ({ ok: true, errors: [] }),
    });

    expect(result).toEqual({ ok: true, value: { meta: { status: 'ready' }, items: ['a', 'b'] }, errors: [] });
});

it('strictly decodes JSON Pointer escapes and blocks unsafe decoded segments', () => {
    expect(decodePointer('/a~0b/c~1d')).toEqual(['a~b', 'c/d']);
    expect(() => decodePointer('/bad~2escape')).toThrow('Invalid JSON pointer escape');
    expect(() => decodePointer('/safe/__proto__')).toThrow('Blocked JSON pointer');
    expect(() => decodePointer('/safe/constructor')).toThrow('Blocked JSON pointer');
    expect(() => decodePointer('/safe~1branch/prototype')).toThrow('Blocked JSON pointer');
});

it('rejects root operations and changes overlapping locked paths', () => {
    const base = { scene: { title: 'old' }, settings: { theme: 'dark' } };
    const root = applyValidatedPatch({
        state: base,
        patch: { operations: [{ op: 'replace', path: '', value: {} }] },
        policy: { allowedPaths: ['/'], lockedPaths: [] },
        validateState: () => ({ ok: true, errors: [] }),
    });
    const lockedParent = applyValidatedPatch({
        state: base,
        patch: { operations: [{ op: 'replace', path: '/scene', value: {} }] },
        policy: { allowedPaths: ['/scene'], lockedPaths: ['/scene/title'] },
        validateState: () => ({ ok: true, errors: [] }),
    });
    expect(root.ok).toBe(false);
    expect(lockedParent.ok).toBe(false);
});

it('treats the root locked path as overlapping every operation path', () => {
    const result = applyValidatedPatch({
        state: { scene: 'old' },
        patch: { operations: [{ op: 'replace', path: '/scene', value: 'new' }] },
        policy: { allowedPaths: ['/'], lockedPaths: ['/'] },
        validateState: () => ({ ok: true, errors: [] }),
    });

    expect(result.ok).toBe(false);
});

it('inserts, replaces, and removes array entries with RFC indices', () => {
    const result = applyValidatedPatch({
        state: { list: ['a', 'c'] },
        patch: { operations: [
            { op: 'add', path: '/list/1', value: 'b' },
            { op: 'add', path: '/list/3', value: 'd' },
            { op: 'replace', path: '/list/0', value: 'A' },
            { op: 'remove', path: '/list/2' },
        ] },
        policy: { allowedPaths: ['/list'], lockedPaths: [] },
        validateState: () => ({ ok: true, errors: [] }),
    });
    expect(result).toEqual({ ok: true, value: { list: ['A', 'b', 'd'] }, errors: [] });
});

it.each(['/list/01', '/list/4', '/list/-1', '/list/9007199254740992'])('rejects invalid add array index %s', (path) => {
    const result = applyValidatedPatch({
        state: { list: ['a'] },
        patch: { operations: [{ op: 'add', path, value: 'b' }] },
        policy: { allowedPaths: ['/list'], lockedPaths: [] },
        validateState: () => ({ ok: true, errors: [] }),
    });
    expect(result.ok).toBe(false);
});

it.each([
    ['replace', '/list/01'],
    ['replace', '/list/1'],
    ['remove', '/list/1'],
    ['remove', '/list/-'],
])('rejects invalid %s array target %s', (op, path) => {
    const result = applyValidatedPatch({
        state: { list: ['a'] },
        patch: { operations: [{ op, path, value: 'b' }] },
        policy: { allowedPaths: ['/list'], lockedPaths: [] },
        validateState: () => ({ ok: true, errors: [] }),
    });
    expect(result.ok).toBe(false);
});

it('is atomic and keeps state and patch caller-owned', () => {
    const state = { profile: { name: 'Mira', trust: 20 } };
    const patch = { operations: [
        { op: 'replace', path: '/profile/trust', value: 25 },
        { op: 'remove', path: '/profile/missing' },
    ] };
    const policy = { allowedPaths: ['/profile'], lockedPaths: [] };
    const result = applyValidatedPatch({
        state,
        patch,
        policy,
        validateState: () => ({ ok: true, errors: [] }),
    });
    expect(result.ok).toBe(false);
    expect(state).toEqual({ profile: { name: 'Mira', trust: 20 } });
    expect(patch.operations[0].value).toBe(25);
    expect(policy).toEqual({ allowedPaths: ['/profile'], lockedPaths: [] });
});

it('returns cloned Error-shaped errors for invalid input and validator failures', () => {
    const validationErrors = [{ message: 'trust invalid' }];
    const rejected = applyValidatedPatch({
        state: { trust: 20 }, patch: { operations: [{ op: 'replace', path: '/trust', value: 25 }] },
        policy: { allowedPaths: ['/trust'], lockedPaths: [] },
        validateState: () => ({ ok: false, errors: validationErrors }),
    });
    rejected.errors[0].message = 'mutated';
    expect(validationErrors[0].message).toBe('trust invalid');
    expect(rejected.value).toBeUndefined();

    for (const options of [
        { state: {}, patch: null, policy: {}, validateState: () => ({ ok: true, errors: [] }) },
        { state: {}, patch: { operations: [] }, policy: null, validateState: () => ({ ok: true, errors: [] }) },
        { state: {}, patch: { operations: [] }, policy: { allowedPaths: [], lockedPaths: [] }, validateState: () => { throw 'validator boom'; } },
    ]) {
        const result = applyValidatedPatch(options);
        expect(result).toMatchObject({ ok: false, errors: [{ message: expect.any(String) }] });
    }
});

it('rejects malformed validator results and normalizes its failure errors', () => {
    const options = { state: { trust: 20 }, patch: { operations: [] }, policy: { allowedPaths: [], lockedPaths: [] } };

    for (const validateState of [
        () => null,
        () => ({ ok: 'yes', errors: [] }),
        () => ({ ok: false }),
        () => ({ ok: true, errors: [{ message: 'contradiction' }] }),
    ]) {
        const result = applyValidatedPatch({ ...options, validateState });
        expect(result).toMatchObject({ ok: false, errors: [{ message: expect.any(String) }] });
    }

    const normalized = applyValidatedPatch({
        ...options,
        validateState: () => ({ ok: false, errors: [{ message: 42 }, Object.create(null), 'plain failure'] }),
    });
    expect(normalized).toEqual({
        ok: false,
        errors: [{ message: '42' }, { message: 'Unable to stringify error' }, { message: 'plain failure' }],
    });
});

it('returns an Error-shaped result even for an unstringifiable thrown value', () => {
    const result = applyValidatedPatch({
        state: {}, patch: { operations: [] }, policy: { allowedPaths: [], lockedPaths: [] },
        validateState: () => { throw Object.create(null); },
    });

    expect(result).toEqual({ ok: false, errors: [{ message: 'Unable to stringify error' }] });
});

it('rejects sparse validator error arrays without returning holes', () => {
    const result = applyValidatedPatch({
        state: {}, patch: { operations: [] }, policy: { allowedPaths: [], lockedPaths: [] },
        validateState: () => ({ ok: false, errors: new Array(1) }),
    });

    expect(result).toEqual({ ok: false, errors: [{ message: 'Invalid state validator result' }] });
});
