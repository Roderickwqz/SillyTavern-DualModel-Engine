import { expect, it, vi } from 'vitest';
import Ajv from 'ajv';
import d20Schema from '../../schemas/d20.schema.json';
import { createOrchestrator } from '../../src/orchestrator.js';
import { createToolRegistry } from '../../src/tool-registry.js';
import { createCheckLedger } from '../../src/check-ledger.js';
import { createStateStore } from '../../src/state-store.js';
import { createStateValidator } from '../../src/state-validator.js';
import { applyValidatedPatch } from '../../src/json-patch.js';
import { createChatTaskQueue } from '../../src/task-queue.js';
import { narrativePreset } from '../../src/rules/narrative.js';
import { d20LitePreset } from '../../src/rules/d20-lite.js';
import { d20TestState } from '../fixtures/d20.js';

const damage = { target: 'player', expression: '1d6', damageType: 'fire', reason: 'trap' };
const patch = { base_version: 0, operations: [{ op: 'add', path: '/inventory/-', value: 'recorded', reason: 'record' }] };
function validator(name) { const ajv = new Ajv(); const fn = ajv.compile({ $ref: `#/$defs/${name}`, ...d20Schema }); return input => ({ ok: fn(input), errors: fn.errors ?? [] }); }
function host({ saveChat = vi.fn(async () => {}), responsePatch = patch, commit } = {}) {
    const user = { is_user: true, mes: 'go', extra: { dualModelEngine: { messageId: 'u1' } } };
    const context = { chatId: 'chat-a', groupId: null, chat: [user], chatMetadata: { dualModelEngine: { schemaVersion: 1, stateVersion: 0, headRevision: 0, activeSnapshot: d20TestState(), activeRef: null, taskStatus: { state: 'idle', requestId: null }, lastCommittedRequestId: null, preset: { id: 'd20-lite' } } } };
    const definitions = new Map(); const adapter = { events: {}, getContext: () => context, saveChat, registerTool: definition => definitions.set(definition.name, definition) };
    const realStore = createStateStore({ adapter, makeId: () => 'generated', hashText: async text => `hash:${text}` }); const store = commit ? { ...realStore, commitSegment: commit } : realStore;
    const stateValidator = createStateValidator({ presets: [narrativePreset, d20LitePreset] }); const queue = createChatTaskQueue(); const ledger = createCheckLedger({ makeId: () => 'damage-1', now: () => 'now' }); const order = [];
    const originalCommit = store.commitSegment.bind(store); store.commitSegment = async input => { order.push('store'); return originalCommit(input); };
    const originalLedgerCommit = ledger.commit.bind(ledger); ledger.commit = records => { order.push('ledger'); return originalLedgerCommit(records); };
    const deps = { adapter, store, queue, ledger, validator: stateValidator, applyPatch: applyValidatedPatch, getConfig: () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'd20-lite', adjudication: 'automatic-tool', injectionBudget: 1 }), getPreset: () => d20LitePreset, hasProfile: () => true, ensureMessageId: message => (message.extra.dualModelEngine ??= {}).messageId ??= 'a1', makeId: (() => { let i = 0; return () => `r${++i}`; })(), promptInjector: { refresh: vi.fn(async () => {}), clear: vi.fn() }, modelService: { requestPatch: vi.fn(async _input => ({ patch: responsePatch })) }, getChecks: () => [], recordDiagnostic: vi.fn() };
    const orchestrator = createOrchestrator(deps); const registry = createToolRegistry({ adapter, getConfig: deps.getConfig, getActiveGeneration: orchestrator.getActiveGeneration, validateCheck: validator('checkInput'), validateDamage: validator('damageInput'), ledger, resolveCheck: async () => ({ total: 1 }), resolveDamage: async (_input, state) => { const next = structuredClone(state); next.actors.player.hp.current = 9; next.actors.player.hp.temporary = 0; return { state: next, audit: { total: 4, hpAfter: 9 } }; } }); registry.register();
    return { context, definitions, deps, store, ledger, order, orchestrator, addAssistant() { context.chat.push({ is_user: false, mes: 'answer', extra: {}, swipe_id: 0, swipe_info: [{ extra: {} }] }); } };
}

it('commits authoritative D20 damage atomically after the store and locks rule-owned paths', async () => {
    const subject = host(); await subject.orchestrator.beforeGeneration('normal'); await subject.definitions.get('DualModelApplyD20Damage').action(damage); subject.addAssistant(); await subject.orchestrator.afterGeneration(); await subject.deps.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(subject.ledger.list()).toHaveLength(1));
    expect(subject.deps.recordDiagnostic.mock.calls).toEqual([]);
    expect(subject.deps.modelService.requestPatch).toHaveBeenCalledWith(expect.objectContaining({ oldState: expect.objectContaining({ actors: expect.objectContaining({ player: expect.objectContaining({ hp: expect.objectContaining({ current: 9 }) }) }) }) }));
    const segment = subject.context.chat.at(-1).swipe_info[0].extra.dualModelEngine.branch.segments[0]; expect(segment.checks).toEqual(subject.ledger.list()); expect(segment.postSnapshot).toEqual(subject.context.chatMetadata.dualModelEngine.activeSnapshot); expect(subject.order).toEqual(['store', 'ledger']);
});

it('rejects recorder changes to rule-owned actor state before any transaction commits', async () => {
    const subject = host({ responsePatch: { base_version: 0, operations: [{ op: 'replace', path: '/actors/player/hp/current', value: 1, reason: 'cheat' }] } }); await subject.orchestrator.beforeGeneration('normal'); await subject.definitions.get('DualModelApplyD20Damage').action(damage); subject.addAssistant(); await subject.orchestrator.afterGeneration(); await subject.deps.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(subject.deps.recordDiagnostic).toHaveBeenCalled());
    expect(subject.store.getBranch(subject.context.chat.at(-1), 0)).toMatchObject({ status: 'stale', segments: [] }); expect(subject.ledger.list()).toEqual([]); expect(subject.context.chatMetadata.dualModelEngine.activeSnapshot.actors.player.hp.current).toBe(10);
});

it.each(['head-conflict', 'state-conflict', 'stale-message'])('does not publish staged D20 effects when commit returns %s', async reason => {
    const commit = vi.fn(async () => ({ ok: false, reason })); const subject = host({ commit }); await subject.orchestrator.beforeGeneration('normal'); await subject.definitions.get('DualModelApplyD20Damage').action(damage); subject.addAssistant(); await subject.orchestrator.afterGeneration(); await subject.deps.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(commit).toHaveBeenCalledOnce());
    expect(commit).toHaveBeenCalledOnce(); expect(subject.ledger.list()).toEqual([]); expect(subject.context.chatMetadata.dualModelEngine.activeSnapshot.actors.player.hp.current).toBe(10);
});

it('rolls back real store metadata and message branches when save rejects after staged damage', async () => {
    const subject = host({ saveChat: vi.fn(async () => { throw new Error('disk'); }) }); const before = structuredClone(subject.context.chatMetadata.dualModelEngine); await subject.orchestrator.beforeGeneration('normal'); await subject.definitions.get('DualModelApplyD20Damage').action(damage); subject.addAssistant(); await subject.orchestrator.afterGeneration(); await subject.deps.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(subject.deps.recordDiagnostic).toHaveBeenCalled());
    expect(subject.ledger.list()).toEqual([]); expect(subject.context.chatMetadata.dualModelEngine).toEqual(before); expect(subject.context.chat.at(-1).extra.dualModelEngine).toEqual({ messageId: 'a1' });
});

it('discards a valid staged effect after a later invalid tool input without committing', async () => {
    const subject = host(); await subject.orchestrator.beforeGeneration('normal'); await subject.definitions.get('DualModelApplyD20Damage').action(damage); await expect(subject.definitions.get('DualModelApplyD20Damage').action({ ...damage, expression: 'bad' })).rejects.toThrow(); subject.addAssistant(); await subject.orchestrator.afterGeneration();
    expect(subject.ledger.list()).toEqual([]); expect(subject.context.chatMetadata.dualModelEngine.activeSnapshot.actors.player.hp.current).toBe(10);
});
