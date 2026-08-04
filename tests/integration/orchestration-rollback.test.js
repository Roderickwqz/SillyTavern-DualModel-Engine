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
const recorderPatch = baseVersion => ({ base_version: baseVersion, operations: [{ op: 'add', path: '/inventory/-', value: 'recorded', reason: 'Recorder observation' }] });
function branch(id, baseSnapshot, snapshot) { return { branchId: id, baseStateVersion: baseSnapshot.version, baseSnapshot, status: 'committed', segments: [{ postSnapshot: snapshot, assistantTextHash: 'hash' }] }; }
function assistant(id, text, branches, selected = 0) { return { is_user: false, mes: text, swipe_id: selected, extra: { dualModelEngine: { messageId: id, branch: structuredClone(branches[selected]) } }, swipe_info: branches.map(item => ({ extra: { dualModelEngine: { messageId: id, branch: structuredClone(item) } } })) }; }
function host({ ignoreAbort = false, hashText = async () => 'hash' } = {}) {
    const listeners = new Map(); const state0 = initial(); const state1 = { ...initial(), version: 1 }; const state2 = { ...initial(), version: 2 };
    const prior = assistant('prior', 'prior', [branch('prior-branch', state0, state1)]); const target = assistant('target', 'target', [branch('target-branch', state1, state2)]);
    const context = { chatId: 'chat-a', chat: [{ is_user: true, mes: 'go', extra: { dualModelEngine: { messageId: 'u' } } }, prior, target], chatMetadata: { dualModelEngine: { schemaVersion: 1, stateVersion: 2, headRevision: 7, initialSnapshot: state0, activeSnapshot: state2, activeRef: { messageId: 'target', swipeId: 0, branchId: 'target-branch' }, preset: { id: 'narrative', version: 1 }, taskStatus: { state: 'idle', requestId: null }, lastCommittedRequestId: null } } };
    const adapter = { events: { CHAT_CHANGED: 'chat-changed', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', MESSAGE_SWIPED: 'swiped', MESSAGE_DELETED: 'deleted', GENERATION_STOPPED: 'stopped' }, getContext: () => context, saveChat: vi.fn(async () => {}), on: (name, fn) => listeners.set(name, fn), off: (name, _fn) => listeners.delete(name) };
    const store = createStateStore({ adapter, hashText }); const queue = createChatTaskQueue(); const manager = createRollbackManager({ adapter, store, queue }); manager.bind();
    let resolve; let reject; const recorder = vi.fn(({ signal }) => new Promise((done, no) => { resolve = done; reject = no; if (!ignoreAbort) signal.addEventListener('abort', () => no(signal.reason), { once: true }); }));
    const promptInjector = { refresh: vi.fn(async () => {}), clear: vi.fn() }; const orchestrator = createOrchestrator({ adapter, store, queue, rollbackManager: manager, getConfig: () => ({ enabled: true, recorderProfileId: 'rec', rulePresetId: 'narrative', injectionBudget: 1 }), getPreset: () => narrativePreset, hasProfile: () => true, ensureMessageId: message => message.extra.dualModelEngine.messageId, makeId: () => crypto.randomUUID(), promptInjector, formatReusableChecks: checks => checks.map(check => `reuse:${check.checkId}:${check.outcome}`).join('|'), modelService: { requestPatch: recorder }, validator: createStateValidator({ presets: [narrativePreset] }), applyPatch: applyValidatedPatch, getChecks: () => [], recordDiagnostic: vi.fn() }); orchestrator.start();
    return { context, manager, queue, recorder, resolve: value => resolve(value), reject: error => reject(error), orchestrator, adapter, promptInjector, emit: (name, value) => listeners.get(name)?.(value) };
}

it('C8 GENERATION_STOPPED repairs a same-chat regenerate deletion after Recorder has begun', async () => {
    const subject = host(); const abort = vi.spyOn(subject.manager, 'abortReplacement'); await subject.orchestrator.beforeGeneration('regenerate'); subject.context.chat.pop(); await subject.emit('deleted', subject.context.chat.length);
    subject.context.chat.push({ is_user: false, mes: 'replacement', swipe_id: 0, extra: { dualModelEngine: { messageId: 'replacement' } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'replacement' } } }] }); await subject.orchestrator.afterGeneration(); await vi.waitFor(() => expect(subject.recorder).toHaveBeenCalledOnce());
    subject.emit('stopped'); await subject.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(subject.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'prior', swipeId: 0, branchId: 'prior-branch' }));
    expect(subject.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ ...initial(), version: 1 }); expect(subject.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'prior', swipeId: 0, branchId: 'prior-branch' });
    expect(abort).toHaveBeenCalledOnce();
});

it('C3 host clone blank swipe records from the persisted source base and reusable checks', async () => {
    const subject = host(); const target = subject.context.chat.at(-1); const source = structuredClone(target.swipe_info[0].extra.dualModelEngine.branch);
    source.segments[0].checks = [{ kind: 'check', checkId: 'source-check', outcome: 'authoritative' }]; target.swipe_info[0].extra.dualModelEngine.branch = structuredClone(source); target.extra.dualModelEngine.branch = structuredClone(source);
    target.swipe_info.push({ extra: { dualModelEngine: { messageId: 'target' } } }); target.swipe_id = 1;
    expect(subject.emit('swiped', 2)).toEqual({ ok: true, pendingGeneration: true, sourceSwipeId: 0 });
    await subject.orchestrator.beforeGeneration('swipe');
    expect(subject.promptInjector.refresh.mock.calls.at(-1)[0]).toMatchObject({ hardRuleText: 'reuse:source-check:authoritative' });
    await subject.orchestrator.afterGeneration(); await vi.waitFor(() => expect(subject.recorder).toHaveBeenCalledOnce());
    expect(subject.recorder.mock.calls[0][0]).toMatchObject({ baseVersion: source.baseStateVersion, oldState: source.baseSnapshot, checks: source.segments[0].checks });
    subject.resolve({ patch: recorderPatch(source.baseStateVersion) }); await subject.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(target.swipe_info[1].extra.dualModelEngine.branch).toBeTruthy());
    expect(target.swipe_info[1].extra.dualModelEngine.branch.baseSnapshot).toEqual(source.baseSnapshot);
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
    deps.rollbackManager.repairOrphanedHead = async () => { throw new Error('save failed'); }; const unhandled = []; const listener = reason => unhandled.push(reason); process.on('unhandledRejection', listener);
    try { await expect(createOrchestrator(deps).initializeChat()).resolves.toEqual({ enabled: true }); } finally { process.off('unhandledRejection', listener); }
    expect(diagnostics).toContainEqual(expect.objectContaining({ reason: 'orphan-repair-failed' })); expect(unhandled).toEqual([]);
});

it('defers old-chat replacement recovery across CHAT_CHANGED, leaves the new chat clean, then repairs the old orphan on initialization', async () => {
    const subject = host({ ignoreAbort: true }); const oldChat = subject.context.chat; const oldMetadata = subject.context.chatMetadata;
    await subject.orchestrator.beforeGeneration('regenerate'); subject.context.chat.pop(); await subject.emit('deleted', subject.context.chat.length);
    subject.context.chat.push({ is_user: false, mes: 'replacement', swipe_id: 0, extra: { dualModelEngine: { messageId: 'replacement' } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'replacement' } } }] }); await subject.orchestrator.afterGeneration(); await vi.waitFor(() => expect(subject.recorder).toHaveBeenCalledOnce());
    const fresh = { schemaVersion: 1, stateVersion: 0, headRevision: 0, initialSnapshot: initial(), activeSnapshot: initial(), activeRef: null, preset: { id: 'narrative', version: 1 }, taskStatus: { state: 'idle', requestId: null }, lastCommittedRequestId: null };
    subject.context.chatId = 'chat-b'; subject.context.chat = [{ is_user: true, mes: 'new', extra: { dualModelEngine: { messageId: 'new-u' } } }]; subject.context.chatMetadata = { dualModelEngine: fresh }; await subject.emit('chat-changed');
    subject.resolve({ patch: recorderPatch(1) }); await subject.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(subject.orchestrator.getStatus().activeChatId).toBe('chat-b'));
    expect(fresh).toMatchObject({ stateVersion: 0, activeRef: null }); expect(subject.context.chat).toHaveLength(1);
    subject.context.chatId = 'chat-a'; subject.context.chat = oldChat; subject.context.chatMetadata = oldMetadata; await subject.orchestrator.initializeChat();
    expect(oldMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'prior', swipeId: 0, branchId: 'prior-branch' }); expect(oldMetadata.dualModelEngine.activeSnapshot.version).toBe(1); expect(subject.promptInjector.refresh.mock.calls.at(-1)[0].state).toMatchObject({ version: 1 });
});

it('C8 commits a legal post-delete regenerate replacement and selects its new branch', async () => {
    const subject = host(); const complete = vi.spyOn(subject.manager, 'completeReplacement'); const target = subject.context.chat.at(-1);
    await subject.orchestrator.beforeGeneration('regenerate'); subject.context.chat.pop(); await subject.emit('deleted', subject.context.chat.length);
    const replacement = { is_user: false, mes: 'replacement', swipe_id: 0, extra: { dualModelEngine: { messageId: 'replacement' } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'replacement' } } }] };
    subject.context.chat.push(replacement); await subject.orchestrator.afterGeneration(); await vi.waitFor(() => expect(subject.recorder).toHaveBeenCalledOnce());
    subject.resolve({ patch: recorderPatch(1) }); await subject.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(replacement.swipe_info[0].extra.dualModelEngine.branch).toBeTruthy());
    const branch = replacement.swipe_info[0].extra.dualModelEngine.branch;
    expect(branch).toMatchObject({ status: 'committed', baseStateVersion: 1 }); expect(branch.branchId).not.toBe(target.swipe_info[0].extra.dualModelEngine.branch.branchId);
    expect(subject.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'replacement', swipeId: 0, branchId: branch.branchId }); expect(complete).toHaveBeenCalledOnce();
});

it('settles a rejected Recorder through the real queue by aborting once and restoring the prior branch without unhandled rejections', async () => {
    const subject = host(); const abort = vi.spyOn(subject.manager, 'abortReplacement'); const unhandled = []; const listener = reason => unhandled.push(reason); process.on('unhandledRejection', listener);
    try {
        await subject.orchestrator.beforeGeneration('regenerate'); subject.context.chat.pop(); await subject.emit('deleted', subject.context.chat.length);
        subject.context.chat.push({ is_user: false, mes: 'replacement', swipe_id: 0, extra: { dualModelEngine: { messageId: 'replacement' } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'replacement' } } }] });
        await subject.orchestrator.afterGeneration(); await vi.waitFor(() => expect(subject.recorder).toHaveBeenCalledOnce()); subject.reject(new Error('offline')); await subject.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(subject.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'prior', swipeId: 0, branchId: 'prior-branch' }));
    } finally { process.off('unhandledRejection', listener); }
    expect(abort).toHaveBeenCalledOnce(); expect(unhandled).toEqual([]);
});

it('rejects a replacement rebase when the captured source activeRef changes despite an unchanged head revision', async () => {
    const subject = host(); const commit = vi.spyOn(subject.manager, 'completeReplacement'); await subject.orchestrator.beforeGeneration('regenerate'); subject.context.chat.pop(); await subject.emit('deleted', subject.context.chat.length);
    const replacement = { is_user: false, mes: 'replacement', swipe_id: 0, extra: { dualModelEngine: { messageId: 'replacement' } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'replacement' } } }] };
    subject.context.chat.push(replacement); await subject.orchestrator.afterGeneration(); await vi.waitFor(() => expect(subject.recorder).toHaveBeenCalledOnce());
    subject.context.chatMetadata.dualModelEngine.activeRef = { messageId: 'prior', swipeId: 0, branchId: 'prior-branch' }; subject.resolve({ patch: recorderPatch(1) }); await subject.queue.waitForIdle('chat-a');
    expect(replacement.swipe_info[0].extra.dualModelEngine.branch).toBeUndefined(); expect(subject.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'prior', swipeId: 0, branchId: 'prior-branch' }); expect(commit).not.toHaveBeenCalled();
});

it('stopping after Recorder resolves but before its deferred hash completes cannot commit and settles replacement once', async () => {
    let releaseHash; let hashStarted = false; const subject = host({ ignoreAbort: true, hashText: () => new Promise(resolve => { hashStarted = true; releaseHash = resolve; }) }); const abort = vi.spyOn(subject.manager, 'abortReplacement'); const unhandled = []; const listener = reason => unhandled.push(reason); process.on('unhandledRejection', listener);
    await subject.orchestrator.beforeGeneration('regenerate'); subject.context.chat.pop(); await subject.emit('deleted', subject.context.chat.length);
    const replacement = { is_user: false, mes: 'replacement', swipe_id: 0, extra: { dualModelEngine: { messageId: 'replacement' } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'replacement' } } }] };
    subject.context.chat.push(replacement); await subject.orchestrator.afterGeneration(); await vi.waitFor(() => expect(subject.recorder).toHaveBeenCalledOnce()); subject.resolve({ patch: recorderPatch(1) }); await vi.waitFor(() => expect(hashStarted).toBe(true));
    try { subject.emit('stopped'); releaseHash('hash'); await subject.queue.waitForIdle('chat-a'); await vi.waitFor(() => expect(subject.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'prior', swipeId: 0, branchId: 'prior-branch' })); } finally { process.off('unhandledRejection', listener); }
    expect(replacement.swipe_info[0].extra.dualModelEngine.branch).toBeUndefined(); expect(abort).toHaveBeenCalledOnce(); expect(unhandled).toEqual([]);
});
