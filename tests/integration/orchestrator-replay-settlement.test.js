import { expect, it, vi } from 'vitest';
import { createOrchestrator } from '../../src/orchestrator.js';
import { createStateStore } from '../../src/state-store.js';
import { createStateValidator } from '../../src/state-validator.js';
import { applyValidatedPatch } from '../../src/json-patch.js';
import { narrativePreset } from '../../src/rules/narrative.js';

const initial = () => structuredClone(narrativePreset.initialState);
const patch = { base_version: 0, operations: [{ op: 'add', path: '/inventory/-', value: 'key', reason: 'Recorder observed a key' }] };
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
function deferred() { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function message(id, mes, is_user = false) { return { is_user, mes, swipe_id: 0, extra: { dualModelEngine: { messageId: id } }, swipe_info: is_user ? undefined : [{ extra: { dualModelEngine: { messageId: id } } }] }; }
function replayFixture() {
    const user = message('u1', 'visible user text', true); const assistant = message('a1', 'visible assistant text');
    const envelope = { schemaVersion: 1, stateVersion: 0, headRevision: 7, preset: { id: 'narrative' }, activeSnapshot: initial(), initialSnapshot: initial(), taskStatus: { state: 'idle', requestId: null } };
    const context = { chatId: 'chat-a', chat: [user, assistant], chatMetadata: { dualModelEngine: envelope } }; const model = deferred(); const validator = createStateValidator({ presets: [narrativePreset] });
    const adapter = { events: {}, getContext: () => context, saveChat: vi.fn(async () => {}), on: vi.fn(), off: vi.fn() };
    const store = createStateStore({ adapter, makeId: () => 'store-id', hashText: async () => 'hash' });
    assistant.swipe_info[0].extra.dualModelEngine.branch = { branchId: 'branch-a', baseStateVersion: 0, baseSnapshot: initial(), status: 'committed', segments: [{ checks: [{ kind: 'check', checkId: 'source', outcome: 'authoritative' }] }] };
    const deps = { adapter, store, queue: { waitForIdle: async () => {}, getStatus: () => ({ state: 'idle' }) }, getConfig: () => ({ enabled: true, recorderProfileId: 'rec', rulePresetId: 'narrative', injectionBudget: 9 }), getPreset: () => narrativePreset, hasProfile: () => true, ensureMessageId: item => item.extra.dualModelEngine.messageId, makeId: () => 'replay-request', promptInjector: { clear: vi.fn(), refresh: vi.fn() }, modelService: { requestPatch: vi.fn(() => model.promise) }, validator, applyPatch: applyValidatedPatch, getChecks: () => [], recordDiagnostic: vi.fn() };
    return { user, assistant, envelope, context, model, deps, store };
}

it('replays visible text against the captured snapshot and commits the latest assistant object', async () => {
    const host = replayFixture(); const replay = createOrchestrator(host.deps).replayTurn({ messageIndex: 1, swipeId: 0, baseSnapshot: initial() });
    await vi.waitFor(() => expect(host.deps.modelService.requestPatch).toHaveBeenCalledOnce());
    expect(host.deps.modelService.requestPatch.mock.calls[0][0]).toMatchObject({ oldState: initial(), playerText: 'visible user text', assistantText: 'visible assistant text', baseVersion: 0 });
    host.model.resolve({ patch }); await expect(replay).resolves.toEqual({ ok: true, snapshot: { ...initial(), version: 1, inventory: ['key'] }, stateVersion: 1 });
    expect(host.envelope).toMatchObject({ stateVersion: 1, headRevision: 8, activeSnapshot: { version: 1, inventory: ['key'] } });
    expect(host.assistant.swipe_info[0].extra.dualModelEngine.branch.segments[0]).toMatchObject({ assistantTextHash: 'hash', postSnapshot: { version: 1, inventory: ['key'] } });
});

it('rejects replay when the assistant changes while Recorder awaits without validation or commit', async () => {
    const host = replayFixture(); const validate = vi.spyOn(host.deps.validator, 'validatePatch'); const commit = vi.spyOn(host.store, 'commitSegment'); const replay = createOrchestrator(host.deps).replayTurn({ messageIndex: 1, swipeId: 0, baseSnapshot: initial() });
    await vi.waitFor(() => expect(host.deps.modelService.requestPatch).toHaveBeenCalledOnce()); host.assistant.mes = 'edited'; host.model.resolve({ patch });
    await expect(replay).resolves.toEqual({ ok: false, reason: 'assistant-text-mismatch' }); expect(validate).not.toHaveBeenCalled(); expect(commit).not.toHaveBeenCalled();
});

it.each([
    ['rebuilt stable objects', host => { host.context.chat = [structuredClone(host.user), structuredClone(host.assistant)]; }, true],
    ['edited user text', host => { host.user.mes = 'edited user'; }, false],
    ['replaced stable user id', host => { host.context.chat[0] = message('other-user', 'visible user text', true); }, false],
])('replay %s only when both captured identities and text remain stable', async (_name, mutate, accepted) => {
    const host = replayFixture(); const replay = createOrchestrator(host.deps).replayTurn({ messageIndex: 1, swipeId: 0, baseSnapshot: initial() }); await vi.waitFor(() => expect(host.deps.modelService.requestPatch).toHaveBeenCalledOnce());
    mutate(host); host.model.resolve({ patch }); if (accepted) { await expect(replay).resolves.toMatchObject({ ok: true }); expect(host.envelope.activeSnapshot.inventory).toEqual(['key']); } else await expect(replay).resolves.toEqual({ ok: false, reason: 'assistant-text-mismatch' });
});

it('refreshes prepared swipe with authoritative reusable checks and captures its head revision', async () => {
    const user = message('u', 'go', true); const assistant = message('a', 'answer'); const envelope = { stateVersion: 3, headRevision: 11, preset: { id: 'narrative' }, activeSnapshot: initial() }; const context = { chatId: 'chat-a', chat: [user, assistant], chatMetadata: { dualModelEngine: envelope } };
    const prepareSwipeGeneration = vi.fn(() => ({ ok: true, baseSnapshot: initial(), baseStateVersion: 0, expectedHeadRevision: 11, reusableChecks: [{ kind: 'check', checkId: 'same', outcome: 'authoritative' }] })); const refresh = vi.fn(async () => {}); let queued; const queueResult = deferred();
    let recorderChecks; const deps = { adapter: { events: {}, getContext: () => context, on: vi.fn(), off: vi.fn() }, store: { loadEnvelope: () => envelope, getBranch: () => ({ branchId: 'source' }), commitSegment: vi.fn(async () => ({ ok: true })) }, queue: { waitForIdle: async () => {}, getStatus: () => ({ state: 'idle' }), enqueue: (_c, _r, task) => { queued = task; return queueResult.promise; } }, getConfig: () => ({ enabled: true, recorderProfileId: 'r', rulePresetId: 'narrative', injectionBudget: 9 }), getPreset: () => narrativePreset, hasProfile: () => true, ensureMessageId: x => x.extra.dualModelEngine.messageId, makeId: () => 'request', prepareSwipeGeneration, promptInjector: { refresh, clear: vi.fn() }, formatReusableChecks: checks => `AUTHORITATIVE ${checks[0].outcome}; do not check again`, modelService: { requestPatch: vi.fn(async input => { recorderChecks = structuredClone(input.checks); input.checks[0].outcome = 'mutated'; return { patch }; }) }, validator: { validatePatch: () => ({ ok: true, errors: [] }), validateState: () => ({ ok: true, errors: [] }) }, applyPatch: () => ({ ok: true, value: { ...initial(), version: 1, inventory: ['key'] } }), getChecks: () => [{ kind: 'check', checkId: 'same', outcome: 'later' }, { kind: 'check', checkId: 'new', outcome: 'new' }], recordDiagnostic: vi.fn() };
    const subject = createOrchestrator(deps); await subject.beforeGeneration('swipe'); expect(refresh.mock.calls.at(-1)[0].hardRuleText).toContain('AUTHORITATIVE authoritative; do not check again'); await subject.afterGeneration(); queueResult.resolve(await queued({})); await flush();
    expect(prepareSwipeGeneration).toHaveBeenCalledOnce(); expect(deps.store.commitSegment).toHaveBeenCalledWith(expect.objectContaining({ expectedHeadRevision: 11, checks: [{ kind: 'check', checkId: 'same', outcome: 'authoritative' }, { kind: 'check', checkId: 'new', outcome: 'new' }] })); const commitChecks = deps.store.commitSegment.mock.calls[0][0].checks; expect(recorderChecks).toEqual(commitChecks); expect(recorderChecks).not.toBe(commitChecks); expect(deps.modelService.requestPatch.mock.calls[0][0].checks[0].outcome).toBe('mutated');
});

it('settles failed replacement exactly once when queue rejects and abort itself rejects', async () => {
    const host = replayFixture(); const rejection = deferred(); const abort = vi.fn(() => Promise.reject(new Error('abort'))); const complete = vi.fn(); const failed = vi.fn(async () => ({ ok: true })); const diagnostics = [];
    Object.assign(host.deps, { rollbackManager: { abortReplacement: abort, completeReplacement: complete }, store: { loadEnvelope: () => host.envelope, getBranch: () => ({ branchId: 'b' }), markBranchFailed: failed }, queue: { waitForIdle: async () => {}, getStatus: () => ({ state: 'idle' }), enqueue: () => rejection.promise }, recordDiagnostic: value => diagnostics.push(value) });
    const unhandled = []; const listener = reason => unhandled.push(reason); process.on('unhandledRejection', listener); try { const subject = createOrchestrator(host.deps); await subject.beforeGeneration('normal'); await subject.afterGeneration(); rejection.reject(new Error('offline')); await flush(); await flush(); } finally { process.off('unhandledRejection', listener); }
    expect(abort).toHaveBeenCalledOnce(); expect(complete).not.toHaveBeenCalled(); expect(failed).toHaveBeenCalledOnce(); expect(diagnostics.map(x => x.reason)).toContain('replacement-settlement-failed'); expect(unhandled).toEqual([]);
});

it('settles a fulfilled queue failure once, aborting before marking the branch failed', async () => {
    const host = replayFixture(); const abort = vi.fn(); const complete = vi.fn(); const failed = vi.fn(async () => ({ ok: true }));
    Object.assign(host.deps, { rollbackManager: { abortReplacement: abort, completeReplacement: complete }, store: { loadEnvelope: () => host.envelope, getBranch: () => ({ branchId: 'b' }), markBranchFailed: failed }, queue: { waitForIdle: async () => {}, getStatus: () => ({ state: 'idle' }), enqueue: () => Promise.resolve({ ok: false, reason: 'invalid-state' }) } });
    const subject = createOrchestrator(host.deps); await subject.beforeGeneration('normal'); await subject.afterGeneration(); await flush();
    expect(abort).toHaveBeenCalledOnce(); expect(complete).not.toHaveBeenCalled(); expect(failed).toHaveBeenCalledOnce();
});

it('observes a locate-failure abort rejection without an unhandled rejection', async () => {
    const host = replayFixture(); const abort = vi.fn(() => Promise.reject(new Error('abort'))); const diagnostics = [];
    Object.assign(host.deps, { rollbackManager: { abortReplacement: abort }, recordDiagnostic: value => diagnostics.push(value) }); host.context.chat = [host.user];
    const unhandled = []; const listener = reason => unhandled.push(reason); process.on('unhandledRejection', listener); try { const subject = createOrchestrator(host.deps); await subject.beforeGeneration('normal'); await expect(subject.afterGeneration()).resolves.toEqual({ ok: false, reason: 'missing-final-message' }); await flush(); } finally { process.off('unhandledRejection', listener); }
    expect(abort).toHaveBeenCalledOnce(); expect(diagnostics).toContainEqual(expect.objectContaining({ reason: 'missing-final-message' })); expect(unhandled).toEqual([]);
});

it('completes a successful replacement once and treats prompt refresh rejection as diagnostic only', async () => {
    const host = replayFixture(); const complete = vi.fn(); const abort = vi.fn(); const diagnostics = []; let task; const queueResult = deferred();
    Object.assign(host.deps, { rollbackManager: { abortReplacement: abort, completeReplacement: complete }, queue: { waitForIdle: async () => {}, getStatus: () => ({ state: 'idle' }), enqueue: (_c, _r, fn) => { task = fn; return queueResult.promise; } }, store: { loadEnvelope: () => host.envelope, getBranch: () => ({ branchId: 'b' }), commitSegment: vi.fn(async () => ({ ok: true })) }, promptInjector: { refresh: vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('refresh')), clear: vi.fn() }, recordDiagnostic: value => diagnostics.push(value) });
    const subject = createOrchestrator(host.deps); await subject.beforeGeneration('normal'); host.context.chat.push(message('a2', 'answer')); await subject.afterGeneration(); host.model.resolve({ patch }); queueResult.resolve(await task({})); await flush();
    expect(complete).toHaveBeenCalledOnce(); expect(abort).not.toHaveBeenCalled(); expect(diagnostics).toContainEqual(expect.objectContaining({ reason: 'prompt-refresh-failed' }));
});
