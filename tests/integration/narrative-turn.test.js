import { expect, it, vi } from 'vitest';
import { createOrchestrator } from '../../src/orchestrator.js';
import { createNarrativeHost, createOrchestratorTestDependencies } from '../fixtures/fake-host.js';
import { createStateStore } from '../../src/state-store.js';
import { createChatTaskQueue } from '../../src/task-queue.js';

it('waits for Recorder, commits one version, then refreshes the prompt', async () => {
    const user = { is_user: true, mes: 'I give Mira the key.', extra: { dualModelEngine: { messageId: 'u1' } } };
    const assistant = { is_user: false, mes: 'Mira accepts it.', extra: {}, swipe_id: 0, swipe_info: [{ extra: {} }] };
    const store = {
        loadEnvelope: vi.fn(() => ({ stateVersion: 0, headRevision: 0, preset: { id: 'narrative', version: 1 }, activeSnapshot: { version: 0, inventory: ['key'] } })),
        commitSegment: vi.fn().mockResolvedValue({ ok: true, stateVersion: 1 }),
    };
    const dependencies = createOrchestratorTestDependencies({ chat: [user, assistant], store });
    const orchestrator = createOrchestrator(dependencies);
    await orchestrator.beforeGeneration('normal');
    await orchestrator.afterGeneration();
    await dependencies.queue.waitForIdle('chat-a');
    expect(store.commitSegment).toHaveBeenCalledWith(expect.objectContaining({ expectedHeadRevision: 0, baseStateVersion: 0, userMessageId: 'u1', isContinue: false }));
    expect(dependencies.promptInjector.refresh).toHaveBeenCalledTimes(2);
});

it('sends only appended text for continue', async () => {
    const host = createNarrativeHost({ assistantText: 'first' });
    await host.startContinue(); host.setAssistantText('first second'); await host.finishContinue();
    expect(host.lastRecorderInput().assistantText).toBe(' second');
});

it('skips tool intermediaries and keeps one transaction through recursion', async () => {
    const host = createNarrativeHost();
    await host.startTurn(); host.addToolIntermediary();
    expect(await host.emitRecursiveBeforeGeneration()).toMatchObject({ ignored: true, reason: 'tool-recursion' });
    host.addFinalAssistant('The lock opens.'); await host.finishGeneration();
    expect(host.recorderRequests()).toHaveLength(1);
    expect(host.lastRecorderInput().assistantText).toBe('The lock opens.');
});

it('does not capture dry run or unsupported generation', async () => {
    const host = createNarrativeHost();
    expect(await host.orchestrator.beforeGeneration('quiet')).toMatchObject({ ignored: true });
    await host.emitBefore('normal', true); await host.finishGeneration();
    expect(host.recorderRequests()).toHaveLength(0);
});

it('cancels the captured turn on chat change', async () => {
    const host = createNarrativeHost(); await host.startTurn(); host.switchChat('chat-b'); await host.finishGeneration();
    expect(host.recorderRequests()).toHaveLength(0);
});

it('records late conflicts without marking failure', async () => {
    const host = createNarrativeHost({ commit: { ok: false, reason: 'head-conflict' } });
    await host.startTurn(); host.addFinalAssistant('ok'); await host.finishGeneration();
    expect(host.failures).toHaveLength(0); expect(host.diagnostics.at(-1).reason).toBe('head-conflict');
});

it('binds named host events once and unbinds them on stop', () => {
    const dependencies = createOrchestratorTestDependencies();
    dependencies.adapter.events = { CHAT_CHANGED: 'chat', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', GENERATION_STOPPED: 'stopped' };
    const orchestrator = createOrchestrator(dependencies);
    orchestrator.start(); orchestrator.start();
    expect(dependencies.adapter.on).toHaveBeenCalledTimes(4);
    orchestrator.stop();
    expect(dependencies.adapter.off).toHaveBeenCalledTimes(4);
    expect(dependencies.promptInjector.clear).toHaveBeenCalledOnce();
});

it('T6 persists a matching Recorder failure as one stale destination branch', async () => {
    const user = { is_user: true, mes: 'go', extra: { dualModelEngine: { messageId: 'u' } } };
    const assistant = { is_user: false, mes: 'answer', extra: {}, swipe_id: 0, swipe_info: [{ extra: {} }] };
    const context = { chatId: 'chat-a', chat: [user, assistant], chatMetadata: { dualModelEngine: { schemaVersion: 1, stateVersion: 0, headRevision: 0, preset: { id: 'narrative', version: 1 }, activeSnapshot: { version: 0, inventory: [] }, initialSnapshot: { version: 0, inventory: [] }, taskStatus: { state: 'idle', requestId: null } } } };
    const saveChat = vi.fn(async () => {}); let id = 0; const adapter = { events: {}, getContext: () => context, saveChat, on: vi.fn(), off: vi.fn() };
    const store = createStateStore({ adapter, makeId: () => `id${++id}`, hashText: async () => 'hash' }); const diagnostics = [];
    const preset = { id: 'narrative', allowedPaths: ['/inventory'], lockedPaths: ['/version'], injection: [] };
    const queue = createChatTaskQueue(); const orchestrator = createOrchestrator({ adapter, store, queue, getConfig: () => ({ enabled: true, recorderProfileId: 'r', rulePresetId: 'narrative', injectionBudget: 1 }), getPreset: () => preset, hasProfile: () => true, ensureMessageId: message => (message.extra.dualModelEngine ??= {}).messageId ??= `m${++id}`, makeId: () => `id${++id}`, promptInjector: { refresh: vi.fn(async () => {}), clear: vi.fn() }, modelService: { requestPatch: vi.fn(async () => { throw new Error('offline'); }) }, validator: {}, applyPatch: () => ({ ok: true }), getChecks: () => [], recordDiagnostic: value => diagnostics.push(value) });
    const captured = await orchestrator.beforeGeneration('normal'); const before = structuredClone(context.chatMetadata.dualModelEngine.activeSnapshot); await orchestrator.afterGeneration(); await queue.waitForIdle('chat-a'); await Promise.resolve();
    const envelope = context.chatMetadata.dualModelEngine; const branch = assistant.swipe_info[0].extra.dualModelEngine.branch;
    expect(branch).toMatchObject({ branchId: 'id2', status: 'stale' }); expect(envelope.taskStatus).toEqual({ state: 'failed', requestId: captured.requestId }); expect(envelope.headRevision).toBe(1); expect(envelope.stateVersion).toBe(0); expect(envelope.activeSnapshot).toEqual(before); expect(saveChat).toHaveBeenCalledOnce();
});

function deferred() {
    let reject;
    const promise = new Promise((resolve, rejectPromise) => { reject = rejectPromise; });
    return { promise, reject };
}

function createDeferredFailureHost() {
    const user = { is_user: true, mes: 'go', extra: { dualModelEngine: { messageId: 'u' } } };
    const assistant = { is_user: false, mes: 'answer', extra: {}, swipe_id: 0, swipe_info: [{ extra: {} }, { extra: {} }] };
    const envelope = { schemaVersion: 1, stateVersion: 0, headRevision: 0, preset: { id: 'narrative', version: 1 }, activeSnapshot: { version: 0, inventory: [] }, initialSnapshot: { version: 0, inventory: [] }, taskStatus: { state: 'idle', requestId: null } };
    const context = { chatId: 'chat-a', chat: [user, assistant], chatMetadata: { dualModelEngine: envelope } };
    const saveChat = vi.fn(async () => {}); let id = 0; const request = deferred(); const diagnostics = [];
    const adapter = { events: {}, getContext: () => context, saveChat, on: vi.fn(), off: vi.fn() };
    const store = createStateStore({ adapter, makeId: () => `id${++id}`, hashText: async () => 'hash' });
    const queue = createChatTaskQueue(); const preset = { id: 'narrative', allowedPaths: ['/inventory'], lockedPaths: ['/version'], injection: [] };
    const recorder = vi.fn(() => request.promise);
    const orchestrator = createOrchestrator({ adapter, store, queue, getConfig: () => ({ enabled: true, recorderProfileId: 'r', rulePresetId: 'narrative', injectionBudget: 1 }), getPreset: () => preset, hasProfile: () => true, ensureMessageId: message => (message.extra.dualModelEngine ??= {}).messageId ??= `m${++id}`, makeId: () => `id${++id}`, promptInjector: { refresh: vi.fn(async () => {}), clear: vi.fn() }, modelService: { requestPatch: recorder }, validator: {}, applyPatch: () => ({ ok: true }), getChecks: () => [], recordDiagnostic: value => diagnostics.push(value) });
    return { assistant, context, diagnostics, envelope, orchestrator, queue, recorder, request, saveChat };
}

async function beginDeferredRecorder(host) {
    await host.orchestrator.beforeGeneration('normal');
    await host.orchestrator.afterGeneration();
    await vi.waitFor(() => expect(host.orchestrator.getStatus().queue.state).toBe('pending'));
    await vi.waitFor(() => expect(host.recorder).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(host.context.chat.at(-1).extra.dualModelEngine.messageId).toBeTruthy());
}

async function settleDeferredRecorder(host) {
    host.request.reject(new Error('offline'));
    await host.queue.waitForIdle('chat-a');
    await Promise.resolve();
    await Promise.resolve();
}

it('records branch-conflict without mutating an unrelated selected branch after a deferred Recorder rejection', async () => {
    const host = createDeferredFailureHost(); await beginDeferredRecorder(host);
    const other = { branchId: 'other', baseStateVersion: 0, baseSnapshot: { version: 0, inventory: ['other'] }, segments: [], status: 'committed' };
    host.assistant.swipe_info[0].extra.dualModelEngine = { messageId: host.assistant.extra.dualModelEngine.messageId, branch: other };
    host.assistant.extra.dualModelEngine = structuredClone(host.assistant.swipe_info[0].extra.dualModelEngine);
    const beforeEnvelope = structuredClone(host.envelope); const beforeOther = structuredClone(other);
    const unhandled = []; const listener = reason => unhandled.push(reason); process.on('unhandledRejection', listener);
    try { await settleDeferredRecorder(host); } finally { process.off('unhandledRejection', listener); }
    expect(host.diagnostics.at(-1)).toMatchObject({ reason: 'recorder-failed', failureResult: { ok: false, reason: 'branch-conflict' } });
    expect(host.assistant.swipe_info[0].extra.dualModelEngine.branch).toEqual(beforeOther);
    expect(host.assistant.swipe_info[0].extra.dualModelEngine.branch.status).not.toBe('stale');
    expect(host.envelope).toEqual(beforeEnvelope); expect(host.saveChat).not.toHaveBeenCalled(); expect(unhandled).toEqual([]);
});

it('records stale-swipe without mutating either swipe after a deferred Recorder rejection', async () => {
    const host = createDeferredFailureHost(); await beginDeferredRecorder(host);
    const branches = ['first', 'second'].map(branchId => ({ branchId, baseStateVersion: 0, baseSnapshot: { version: 0, inventory: [branchId] }, segments: [], status: 'committed' }));
    for (const [swipeId, branch] of branches.entries()) host.assistant.swipe_info[swipeId].extra.dualModelEngine = { messageId: host.assistant.extra.dualModelEngine.messageId, branch };
    host.assistant.swipe_id = 1; host.assistant.extra.dualModelEngine = structuredClone(host.assistant.swipe_info[1].extra.dualModelEngine);
    const beforeEnvelope = structuredClone(host.envelope); const beforeBranches = structuredClone(branches);
    await settleDeferredRecorder(host);
    expect(host.diagnostics.at(-1)).toMatchObject({ reason: 'recorder-failed', failureResult: { ok: false, reason: 'stale-swipe' } });
    expect(host.assistant.swipe_info.map(swipe => swipe.extra.dualModelEngine.branch)).toEqual(beforeBranches);
    expect(host.assistant.swipe_info.every(swipe => swipe.extra.dualModelEngine.branch.status !== 'stale')).toBe(true);
    expect(host.envelope).toEqual(beforeEnvelope); expect(host.saveChat).not.toHaveBeenCalled();
});

it('records stale-message without mutating a replacement message after a deferred Recorder rejection', async () => {
    const host = createDeferredFailureHost(); await beginDeferredRecorder(host);
    const replacement = { is_user: false, mes: 'replacement', extra: { dualModelEngine: { messageId: 'replacement' } }, swipe_id: 0, swipe_info: [{ extra: { dualModelEngine: { messageId: 'replacement', branch: { branchId: 'other', status: 'committed', segments: [] } } } }] };
    host.context.chat[1] = replacement;
    const beforeEnvelope = structuredClone(host.envelope); const beforeReplacement = structuredClone(replacement);
    await settleDeferredRecorder(host);
    expect(host.diagnostics.at(-1)).toMatchObject({ reason: 'recorder-failed', failureResult: { ok: false, reason: 'stale-message' } });
    expect(replacement).toEqual(beforeReplacement); expect(host.envelope).toEqual(beforeEnvelope); expect(host.saveChat).not.toHaveBeenCalled();
});
