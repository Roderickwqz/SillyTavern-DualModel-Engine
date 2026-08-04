import { expect, it, vi } from 'vitest';
import { createOrchestrator } from '../../src/orchestrator.js';
import { createRollbackManager } from '../../src/rollback-manager.js';
import { createStateStore } from '../../src/state-store.js';
import { createChatTaskQueue } from '../../src/task-queue.js';
import { createStateValidator } from '../../src/state-validator.js';
import { applyValidatedPatch } from '../../src/json-patch.js';
import { narrativePreset } from '../../src/rules/narrative.js';

const initial = () => structuredClone(narrativePreset.initialState);
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
function branch(id, baseSnapshot, snapshot) { return { branchId: id, baseStateVersion: baseSnapshot.version, baseSnapshot, status: 'committed', segments: [{ postSnapshot: snapshot, assistantTextHash: 'hash' }] }; }
function assistant(id, text, branches, selected = 0) { return { is_user: false, mes: text, swipe_id: selected, extra: { dualModelEngine: { messageId: id, branch: structuredClone(branches[selected]) } }, swipe_info: branches.map(item => ({ extra: { dualModelEngine: { messageId: id, branch: structuredClone(item) } } })) }; }
function host({ ignoreAbort = false } = {}) {
    const listeners = new Map(); const state0 = initial(); const state1 = { ...initial(), version: 1 }; const state2 = { ...initial(), version: 2 };
    const prior = assistant('prior', 'prior', [branch('prior-branch', state0, state1)]); const target = assistant('target', 'target', [branch('target-branch', state1, state2)]);
    const context = { chatId: 'chat-a', chat: [{ is_user: true, mes: 'go', extra: { dualModelEngine: { messageId: 'u' } } }, prior, target], chatMetadata: { dualModelEngine: { schemaVersion: 1, stateVersion: 2, headRevision: 7, initialSnapshot: state0, activeSnapshot: state2, activeRef: { messageId: 'target', swipeId: 0, branchId: 'target-branch' }, preset: { id: 'narrative', version: 1 }, taskStatus: { state: 'idle', requestId: null }, lastCommittedRequestId: null } } };
    const adapter = { events: { MESSAGE_SWIPED: 'swiped', MESSAGE_DELETED: 'deleted', GENERATION_STOPPED: 'stopped' }, getContext: () => context, saveChat: vi.fn(async () => {}), on: (name, fn) => listeners.set(name, fn), off: (name, _fn) => listeners.delete(name) };
    const store = createStateStore({ adapter, hashText: async () => 'hash' }); const queue = createChatTaskQueue(); const manager = createRollbackManager({ adapter, store, queue }); manager.bind();
    let resolve; const recorder = vi.fn(({ signal }) => new Promise((done, reject) => { resolve = done; if (!ignoreAbort) signal.addEventListener('abort', () => reject(signal.reason), { once: true }); }));
    const orchestrator = createOrchestrator({ adapter, store, queue, rollbackManager: manager, getConfig: () => ({ enabled: true, recorderProfileId: 'rec', rulePresetId: 'narrative', injectionBudget: 1 }), getPreset: () => narrativePreset, hasProfile: () => true, ensureMessageId: message => message.extra.dualModelEngine.messageId, makeId: () => crypto.randomUUID(), promptInjector: { refresh: vi.fn(async () => {}), clear: vi.fn() }, modelService: { requestPatch: recorder }, validator: createStateValidator({ presets: [narrativePreset] }), applyPatch: applyValidatedPatch, getChecks: () => [], recordDiagnostic: vi.fn() }); orchestrator.start();
    return { context, manager, queue, recorder, resolve: value => resolve(value), orchestrator, emit: (name, value) => listeners.get(name)?.(value) };
}

it('C8 GENERATION_STOPPED repairs a same-chat regenerate deletion after Recorder has begun', async () => {
    const subject = host(); const abort = vi.spyOn(subject.manager, 'abortReplacement'); await subject.orchestrator.beforeGeneration('regenerate'); subject.context.chat.pop(); await subject.emit('deleted', subject.context.chat.length);
    subject.context.chat.push({ is_user: false, mes: 'replacement', swipe_id: 0, extra: { dualModelEngine: { messageId: 'replacement' } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'replacement' } } }] }); await subject.orchestrator.afterGeneration(); await vi.waitFor(() => expect(subject.recorder).toHaveBeenCalledOnce());
    subject.emit('stopped'); await flush(); await subject.queue.waitForIdle('chat-a'); await flush();
    expect(subject.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ ...initial(), version: 1 }); expect(subject.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'prior', swipeId: 0, branchId: 'prior-branch' });
    expect(abort).toHaveBeenCalledOnce();
});

it('C3 keeps the prior persisted branch as the source when a host clone creates a blank swipe', () => {
    const subject = host(); const target = subject.context.chat.at(-1); const source = structuredClone(target.swipe_info[0].extra.dualModelEngine.branch);
    target.swipe_info.push({ extra: { dualModelEngine: { messageId: 'target' } } }); target.swipe_id = 1;
    expect(subject.emit('swiped', 2)).toEqual({ ok: true, pendingGeneration: true, sourceSwipeId: 0 });
    expect(subject.manager.prepareSwipeGeneration(2, 'swipe')).toMatchObject({ ok: true, baseBranchId: source.branchId, baseSnapshot: source.baseSnapshot, baseStateVersion: source.baseStateVersion });
});

it('C8 ignores a Recorder result that arrives after GENERATION_STOPPED', async () => {
    const subject = host({ ignoreAbort: true }); await subject.orchestrator.beforeGeneration('regenerate'); subject.context.chat.pop(); await subject.emit('deleted', subject.context.chat.length);
    const replacement = { is_user: false, mes: 'replacement', swipe_id: 0, extra: { dualModelEngine: { messageId: 'replacement' } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'replacement' } } }] }; subject.context.chat.push(replacement); await subject.orchestrator.afterGeneration(); await vi.waitFor(() => expect(subject.recorder).toHaveBeenCalledOnce());
    subject.emit('stopped'); subject.resolve({ patch: { base_version: 1, operations: [] } }); await flush(); await subject.queue.waitForIdle('chat-a'); await flush();
    expect(replacement.swipe_info[0].extra.dualModelEngine.branch).toBeUndefined(); expect(subject.context.chatMetadata.dualModelEngine.activeSnapshot.version).toBe(1);
});

it('C9 awaits orphan repair before injecting the repaired envelope and records a rejected repair', async () => {
    const context = { chatId: 'chat-a', chat: [], chatMetadata: { dualModelEngine: { stateVersion: 2, headRevision: 3, preset: { id: 'narrative' }, activeSnapshot: { version: 2 } } } }; const refresh = vi.fn(async () => {}); const diagnostics = [];
    const deps = { adapter: { getContext: () => context, events: {}, on: vi.fn(), off: vi.fn() }, store: { loadEnvelope: () => context.chatMetadata.dualModelEngine }, queue: { getStatus: () => ({ state: 'idle' }), waitForIdle: async () => {} }, rollbackManager: { repairOrphanedHead: async () => { context.chatMetadata.dualModelEngine.activeSnapshot = { version: 1 }; } }, getConfig: () => ({ enabled: true, rulePresetId: 'narrative', injectionBudget: 1 }), getPreset: () => ({ injection: [] }), promptInjector: { refresh, clear: vi.fn() }, recordDiagnostic: item => diagnostics.push(item) };
    await expect(createOrchestrator(deps).initializeChat()).resolves.toEqual({ enabled: true }); expect(refresh).toHaveBeenCalledWith(expect.objectContaining({ state: { version: 1 } }));
    deps.rollbackManager.repairOrphanedHead = async () => { throw new Error('save failed'); }; await expect(createOrchestrator(deps).initializeChat()).resolves.toEqual({ enabled: true }); expect(diagnostics).toContainEqual(expect.objectContaining({ reason: 'orphan-repair-failed' }));
});
