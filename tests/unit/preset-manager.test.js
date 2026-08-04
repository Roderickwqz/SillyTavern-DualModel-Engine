import { expect, it, vi } from 'vitest';
import { createPresetManager } from '../../src/preset-manager.js';
import { createCustomRuleAdapter } from '../../src/rules/custom.js';
import { createRuleEngine } from '../../src/rule-engine.js';
import { narrativePreset } from '../../src/rules/narrative.js';

function validCustomPreset() {
    return { id: 'custom-a', name: 'Custom A', presetVersion: 1, compatibleDataVersions: { minimum: 1, maximum: 1 }, stateSchema: { type: 'object', required: ['version', 'notes'], properties: { version: { type: 'integer', minimum: 0 }, notes: { type: 'array', maxItems: 100, items: { type: 'string' } } }, additionalProperties: false }, initialState: { version: 0, notes: [] }, allowedPaths: ['/notes'], lockedPaths: ['/version'], injection: [], ui: [], d20: null };
}

function manager(overrides = {}) {
    return createPresetManager({ settings: { customPresets: [] }, builtInPresets: [], registerPreset: vi.fn(), unregisterPreset: vi.fn(), save: vi.fn(), getReferences: () => [], stateStore: { describePresetReset: vi.fn(() => ({ branchesRemoved: 1 })), resetForPreset: vi.fn(async () => ({ ok: true })) }, ...overrides });
}

it('keeps built-in preset runtime data deeply frozen', () => {
    expect(Object.isFrozen(narrativePreset.initialState)).toBe(true);
    expect(Object.isFrozen(narrativePreset.injection)).toBe(true);
});

it('rejects executable and bounded-schema hazards before registering', async () => {
    const registerPreset = vi.fn(); const instance = manager({ registerPreset });
    const bad = { ...validCustomPreset(), stateSchema: { $ref: 'https://example.test/schema' } };
    await expect(instance.importPreset(JSON.stringify(bad))).rejects.toThrow('Unsupported schema keyword: $ref');
    expect(registerPreset).not.toHaveBeenCalled();
});

it.each([
    ['too many properties', preset => { preset.stateSchema.properties = Object.fromEntries(Array.from({ length: 501 }, (_, i) => [`f${i}`, { type: 'string' }])); }, 'Schema properties exceed 500'],
    ['unbounded array', preset => { delete preset.stateSchema.properties.notes.maxItems; }, 'Array maxItems must be at most 1000'],
    ['union array without bound', preset => { preset.stateSchema.properties.notes = { type: ['string', 'array'], items: { type: 'string' } }; }, 'Array maxItems must be at most 1000'],
])('rejects %s', async (_name, mutate, message) => {
    const preset = validCustomPreset(); mutate(preset);
    await expect(manager().importPreset(JSON.stringify(preset))).rejects.toThrow(message);
});

it('rejects deleting a missing or built-in preset', async () => {
    const instance = manager({ builtInPresets: [{ id: 'narrative' }] });
    await expect(instance.deletePreset('narrative')).rejects.toThrow('Built-in preset');
    await expect(instance.deletePreset('missing')).rejects.toThrow('Custom preset not found');
});

it('rejects a D20 initial state that violates its invariant', async () => {
    const preset = validCustomPreset(); preset.id = 'custom-d20'; preset.d20 = { actorsPath: '/actors', abilitiesPath: '/abilities', proficiencyBonusPath: '/proficiency', proficientSkillsPath: '/skills', hpPath: '/hp', conditionsPath: '/conditions' };
    preset.stateSchema.properties.actors = { type: 'object', additionalProperties: true }; preset.initialState.actors = { hero: { abilities: {}, proficiency: 0, skills: [], hp: { current: 2, max: 1, temporary: 0 }, conditions: [] } };
    await expect(manager().importPreset(JSON.stringify(preset))).rejects.toThrow('Initial state violates preset invariants');
});

it('persists raw data only and rolls registration back after a failed save', async () => {
    const registerPreset = vi.fn(); const unregisterPreset = vi.fn(); const instance = manager({ registerPreset, unregisterPreset, save: vi.fn().mockRejectedValue(new Error('disk')) });
    await expect(instance.importPreset(JSON.stringify(validCustomPreset()))).rejects.toThrow('disk');
    expect(instance.listPresets()).toEqual([]); expect(unregisterPreset).toHaveBeenCalledWith('custom-a');
});

it('binds character defaults, protects referenced presets, and requires confirmed chat reset', async () => {
    const settings = { customPresets: [] }; const resetForPreset = vi.fn(async () => ({ ok: true }));
    const instance = manager({ settings, getReferences: () => [{ type: 'chat', id: 'a' }], stateStore: { describePresetReset: vi.fn(() => ({ branchesRemoved: 1 })), resetForPreset } });
    await instance.importPreset(JSON.stringify(validCustomPreset()));
    const character = { data: {} }; instance.bindCharacter(character, 'custom-a');
    expect(character.data.extensions.dualModelEngine).toEqual({ rulePresetId: 'custom-a', presetVersion: 1 });
    const chat = { dualModelEngine: { preset: { id: 'narrative', version: 1 } } };
    await expect(instance.bindChat(chat, 'custom-a')).resolves.toMatchObject({ reason: 'preset-reset-required' });
    await expect(instance.bindChat(chat, 'custom-a', { confirmedReset: true })).resolves.toEqual({ ok: true });
    expect(resetForPreset).toHaveBeenCalledOnce();
    expect(JSON.parse(instance.exportPreset('custom-a'))).toEqual(validCustomPreset());
    await expect(instance.deletePreset('custom-a')).rejects.toThrow('Preset is still referenced');
});

it('lists stable descriptors for built-in and custom presets', async () => {
    const instance = manager({ builtInPresets: [{ id: 'narrative', name: 'Narrative', presetVersion: 1 }] });
    await instance.importPreset(JSON.stringify(validCustomPreset()));
    expect(instance.listPresets()).toEqual([
        { id: 'narrative', name: 'Narrative', presetVersion: 1, builtIn: true },
        { id: 'custom-a', name: 'Custom A', presetVersion: 1, builtIn: false },
    ]);
});

it.each(['actorsPath', 'abilitiesPath', 'proficiencyBonusPath', 'proficientSkillsPath', 'hpPath', 'conditionsPath'])('rejects invalid declarative D20 %s', async field => {
    const preset = validCustomPreset(); preset.id = 'custom-d20'; preset.d20 = { actorsPath: '/actors', abilitiesPath: '/abilities', proficiencyBonusPath: '/proficiency', proficientSkillsPath: '/skills', hpPath: '/hp', conditionsPath: '/conditions' }; preset.d20[field] = field === 'actorsPath' ? '' : 'relative';
    await expect(manager().importPreset(JSON.stringify(preset))).rejects.toThrow('Invalid D20 path');
});

it('supports escaped actor IDs in declarative D20 accessors', () => {
    const preset = { ...validCustomPreset(), d20: { actorsPath: '/actors', abilitiesPath: '/abilities', proficiencyBonusPath: '/proficiency', proficientSkillsPath: '/skills', hpPath: '/hp', conditionsPath: '/conditions' } };
    const adapter = createCustomRuleAdapter(preset); const state = { actors: { 'hero/~/constructor-safe': { abilities: {}, proficiency: 2, skills: [], hp: { current: 2, max: 2, temporary: 0 }, conditions: [] } } };
    expect(adapter.readActor(state, 'hero/~/constructor-safe').hp.current).toBe(2);
    const special = { actors: Object.fromEntries([['constructor', { abilities: {}, proficiency: 2, skills: [], hp: { current: 1, max: 1, temporary: 0 }, conditions: [] }]]) };
    expect(adapter.readActor(special, 'constructor').hp.current).toBe(1);
});

it('maps declarative D20 paths without executable data', () => {
    const preset = { ...validCustomPreset(), d20: { actorsPath: '/sheet/members', abilitiesPath: '/stats', proficiencyBonusPath: '/proficiency', proficientSkillsPath: '/skills', hpPath: '/vitals', conditionsPath: '/effects', skillAbilities: { stealth: 'dexterity' }, naturalRollPolicy: 'critical' } };
    const adapter = createCustomRuleAdapter(preset); const state = { version: 0, notes: [], sheet: { members: { hero: { stats: { dexterity: 14 }, proficiency: 2, skills: [], vitals: { current: 10, max: 10, temporary: 0 }, effects: [] } } } };
    expect(createRuleEngine({ nextUint32: () => 10, preset: adapter }).applyDamage({ target: 'hero', expression: '1d6' }, state).state.sheet.members.hero.vitals.current).toBe(5);
    expect(Object.isFrozen(adapter)).toBe(true);
});
