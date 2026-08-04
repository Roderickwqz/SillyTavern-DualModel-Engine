import { expect, it, vi } from 'vitest';
import Ajv from 'ajv';
import schema from '../../schemas/d20.schema.json';
import { createCheckLedger } from '../../src/check-ledger.js';
import { createToolRegistry } from '../../src/tool-registry.js';
import { createOrchestrator } from '../../src/orchestrator.js';
import { createOrchestratorTestDependencies } from '../fixtures/fake-host.js';

const check = { actor: 'player', action: ' pick lock ', ability: 'dexterity', skill: 'sleight_of_hand', dc: 12, advantage: 'normal', reason: 'locked door' };
const damage = { target: 'player', expression: '1d6', damageType: 'fire', reason: 'trap' };
function validation(name) { const ajv = new Ajv(); const fn = ajv.compile({ $ref: `#/$defs/${name}` , ...schema }); return input => ({ ok: fn(input), errors: fn.errors ?? [] }); }
function setup(overrides = {}) {
    const definitions = new Map(); const generation = { requestId: 'g1', branchId: 'b1', userMessageId: 'u1', baseSnapshot: { actors: { player: { hp: { current: 10, temporary: 3 } } } }, effectiveConfig: { enabled: true, rulePresetId: 'd20-lite', adjudication: 'automatic-tool' }, pendingRuleRecords: [], pendingRuleEffects: [], ...overrides.generation };
    const adapter = { registerTool: vi.fn(x => definitions.set(x.name, x)), unregisterTool: vi.fn(x => definitions.delete(x)) };
    const resolveCheck = overrides.resolveCheck ?? vi.fn(async () => ({ total: 16 }));
    const resolveDamage = overrides.resolveDamage ?? vi.fn(async (_input, _state) => ({ audit: { rolls: [4], raw: 4, total: 4, absorbed: 3, hpBefore: 10, hpAfter: 9 }, state: { actors: { player: { hp: { current: 9, temporary: 0 } } } } }));
    const registry = createToolRegistry({ adapter, getConfig: () => generation.effectiveConfig, getActiveGeneration: () => overrides.getActiveGeneration?.() ?? generation, validateCheck: validation('checkInput'), validateDamage: validation('damageInput'), resolveCheck, resolveDamage, ledger: createCheckLedger({ makeId: (() => { let n = 0; return () => `c${++n}`; })(), now: () => 'now' }) }); registry.register();
    return { definitions, generation, adapter, resolveCheck, resolveDamage, registry };
}

it('validates exact d20 schemas and uses state-authoritative results', async () => {
    const host = setup(); const tool = host.definitions.get('DualModelResolveD20Check');
    await expect(tool.action({ ...check, total: 99 })).rejects.toThrow();
    expect(await tool.action(check)).toMatchObject({ kind: 'check', result: { total: 16 } });
    expect(host.resolveCheck).toHaveBeenCalledWith(check, host.generation.baseSnapshot);
});

it('deduplicates same-signature concurrent calls and chains damage state', async () => {
    let release; const gate = new Promise(resolve => { release = resolve; }); const resolveCheck = vi.fn(async () => { await gate; return { total: 15 }; });
    const host = setup({ resolveCheck }); const checkTool = host.definitions.get('DualModelResolveD20Check');
    const a = checkTool.action(check); const b = checkTool.action(check); release();
    expect(await a).toEqual(await b); expect(resolveCheck).toHaveBeenCalledOnce();
    const damageTool = host.definitions.get('DualModelApplyD20Damage'); await damageTool.action(damage); await damageTool.action({ ...damage, reason: 'second trap' });
    expect(host.resolveDamage.mock.calls[1][1].actors.player.hp.current).toBe(9);
});

it('serializes different concurrent damage calls against the prior pending effect', async () => {
    const resolveDamage = vi.fn(async (_input, state) => {
        const hpBefore = state.actors.player.hp.current; const next = structuredClone(state); next.actors.player.hp.current--;
        return { state: next, audit: { rolls: [1], raw: 1, total: 1, absorbed: 0, hpBefore, hpAfter: next.actors.player.hp.current } };
    });
    const host = setup({ resolveDamage }); const tool = host.definitions.get('DualModelApplyD20Damage');
    await Promise.all([tool.action(damage), tool.action({ ...damage, reason: 'other source' })]);
    expect(resolveDamage.mock.calls.map(call => call[1].actors.player.hp.current)).toEqual([10, 9]);
    expect(host.generation.pendingRuleEffects.at(-1).nextState.actors.player.hp.current).toBe(8);
});

it('executes identical damage calls independently but serially', async () => {
    const resolveDamage = vi.fn(async (_input, state) => { const next = structuredClone(state); next.actors.player.hp.current--; return { state: next, audit: { rolls: [1], raw: 1, total: 1, absorbed: 0, hpBefore: state.actors.player.hp.current, hpAfter: next.actors.player.hp.current } }; });
    const host = setup({ resolveDamage }); const tool = host.definitions.get('DualModelApplyD20Damage');
    await Promise.all([tool.action(damage), tool.action(damage)]);
    expect(resolveDamage.mock.calls.map(call => call[1].actors.player.hp.current)).toEqual([10, 9]);
});

it.each([
    [{ enabled: false, rulePresetId: 'd20-lite', adjudication: 'automatic-tool' }],
    [{ enabled: true, rulePresetId: 'narrative', adjudication: 'automatic-tool' }],
    [{ enabled: true, rulePresetId: 'd20-lite', adjudication: 'manual' }],
    [{ enabled: true, rulePresetId: 'd20-lite', adjudication: 'confirm' }],
    [{ enabled: true, rulePresetId: 'd20-lite', adjudication: 'enforced-preflight' }],
])('rejects a directly invoked tool when captured config is unauthorized', async effectiveConfig => {
    const host = setup({ generation: { effectiveConfig } });
    await expect(host.definitions.get('DualModelResolveD20Check').action(check)).rejects.toThrow('not authorized');
    expect(host.resolveCheck).not.toHaveBeenCalled(); expect(host.generation.ruleToolFailed).toBe(true);
});

it('marks and discards captured work on validation, resolver, and stale-start failures', async () => {
    const invalid = setup(); await expect(invalid.definitions.get('DualModelResolveD20Check').action({ ...check, total: 1 })).rejects.toThrow(); expect(invalid.generation).toMatchObject({ ruleToolFailed: true, pendingRuleRecords: [], pendingRuleEffects: [] });
    const failed = setup({ resolveDamage: vi.fn(async () => { throw new Error('boom'); }) }); await expect(failed.definitions.get('DualModelApplyD20Damage').action(damage)).rejects.toThrow('boom'); expect(failed.generation).toMatchObject({ ruleToolFailed: true, pendingRuleRecords: [], pendingRuleEffects: [] });
    let current; const stale = setup({ getActiveGeneration: () => current }); current = stale.generation; const action = stale.definitions.get('DualModelResolveD20Check').action(check); current = { ...stale.generation, requestId: 'new', pendingRuleRecords: [], pendingRuleEffects: [] }; await expect(action).rejects.toThrow('generation'); expect(stale.resolveCheck).not.toHaveBeenCalled(); expect(stale.generation.ruleToolFailed).toBe(true);
});

it('only unregisters tools actually registered and remains retry-safe after an unregister failure', () => {
    const adapter = { registerTool: vi.fn(definition => { if (definition.name.endsWith('Damage')) throw new Error('second'); }), unregisterTool: vi.fn() };
    const registry = createToolRegistry({ adapter, getConfig: () => ({}), getActiveGeneration: () => null, validateCheck: () => ({ ok: true }), validateDamage: () => ({ ok: true }), resolveCheck: async () => ({}), resolveDamage: async () => ({}), ledger: createCheckLedger({ makeId: () => 'x', now: () => 'now' }) });
    expect(() => registry.register()).toThrow('second'); expect(adapter.unregisterTool).toHaveBeenCalledWith('DualModelResolveD20Check'); expect(adapter.unregisterTool).not.toHaveBeenCalledWith('DualModelApplyD20Damage');
    registry.unregister(); expect(adapter.unregisterTool).toHaveBeenCalledTimes(1);
});

it('runs failure through the real orchestrator lifecycle without committing staged damage', async () => {
    const user = { is_user: true, mes: 'go', extra: { dualModelEngine: { messageId: 'u1' } } }; const definitions = new Map();
    const deps = createOrchestratorTestDependencies({ chat: [user] }); deps.getConfig = () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'd20-lite', adjudication: 'automatic-tool', injectionBudget: 1 }); deps.getPreset = () => ({ id: 'd20-lite', allowedPaths: ['/inventory'], lockedPaths: ['/version'], ruleLockedPaths: ['/actors'], injection: [] }); deps.adapter.registerTool = definition => definitions.set(definition.name, definition); deps.rollbackManager = { abortReplacement: vi.fn() };
    const ledger = createCheckLedger({ makeId: (() => { let i = 0; return () => `c${++i}`; })(), now: () => 'now' }); deps.ledger = ledger;
    const orchestrator = createOrchestrator(deps); const registry = createToolRegistry({ adapter: deps.adapter, getConfig: deps.getConfig, getActiveGeneration: orchestrator.getActiveGeneration, validateCheck: validation('checkInput'), validateDamage: validation('damageInput'), ledger, resolveCheck: async () => ({ total: 1 }), resolveDamage: vi.fn().mockResolvedValueOnce({ state: { actors: { player: { hp: { current: 9, temporary: 0 } } } }, audit: { rolls: [4], raw: 4, total: 4, absorbed: 3, hpBefore: 10, hpAfter: 9 } }).mockRejectedValueOnce(new Error('damage failure')) }); registry.register();
    await orchestrator.beforeGeneration('normal'); await definitions.get('DualModelApplyD20Damage').action(damage); await expect(definitions.get('DualModelApplyD20Damage').action({ ...damage, reason: 'second' })).rejects.toThrow('damage failure'); deps.context.chat.push({ is_user: false, mes: 'answer', extra: {}, swipe_id: 0, swipe_info: [{ extra: {} }] });
    expect(await orchestrator.afterGeneration()).toMatchObject({ reason: 'rule-tool-failed' }); expect(deps.store.commitSegment).not.toHaveBeenCalled(); expect(ledger.list()).toEqual([]); expect(deps.rollbackManager.abortReplacement).toHaveBeenCalled();
});

it('does not stage late, failed, or reuse-only unmatched tool calls', async () => {
    let current; let release; const gate = new Promise(resolve => { release = resolve; }); const host = setup({ getActiveGeneration: () => current, resolveCheck: vi.fn(async () => { await gate; return { total: 1 }; }) }); current = host.generation;
    const pending = host.definitions.get('DualModelResolveD20Check').action(check); current = { ...host.generation, requestId: 'g2', pendingRuleRecords: [], pendingRuleEffects: [] }; release(); await expect(pending).rejects.toThrow('generation'); expect(host.generation.pendingRuleRecords).toEqual([]);
    const reuse = setup({ generation: { ruleReplayMode: 'reuse-only' } }); await expect(reuse.definitions.get('DualModelResolveD20Check').action(check)).rejects.toThrow('Ordinary regeneration'); expect(reuse.resolveCheck).not.toHaveBeenCalled();
    host.registry.unregister(); expect(host.adapter.unregisterTool).toHaveBeenCalledTimes(2);
});
