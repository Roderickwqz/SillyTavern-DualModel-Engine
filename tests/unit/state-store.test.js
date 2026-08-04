import { expect, it, vi } from 'vitest';
import { createStateStore } from '../../src/state-store.js';

function envelope(overrides = {}) {
    return {
        schemaVersion: 1,
        stateVersion: 2,
        headRevision: 7,
        activeSnapshot: { version: 2, value: 2 },
        activeRef: null,
        taskStatus: { state: 'working', requestId: 'old' },
        lastCommittedRequestId: null,
        ...overrides,
    };
}

function message(branch = null, swipeId = 0) {
    return {
        extra: { dualModelEngine: { messageId: 'm1' } },
        swipe_id: swipeId,
        swipe_info: [{ extra: { dualModelEngine: branch ? { messageId: 'm1', branch } : { messageId: 'm1' } } }],
    };
}

function setup({ current = message(), metadata = envelope(), saveChat = vi.fn().mockResolvedValue(undefined) } = {}) {
    const context = { chatId: 'chat-a', chatMetadata: { dualModelEngine: metadata }, chat: [current] };
    const adapter = { getContext: () => context, saveChat };
    return { context, current, saveChat, store: createStateStore({ adapter, makeId: () => 'generated', hashText: async text => `hash:${text}` }) };
}

function commitInput(current, overrides = {}) {
    return {
        chatId: 'chat-a', message: current, messageId: 'm1', branchId: 'b1', swipeId: 0,
        expectedHeadRevision: 7, baseStateVersion: 2, requestId: 'r1', userMessageId: 'u1',
        baseSnapshot: { version: 2, value: 2 }, patch: { operations: [] }, checks: [{ checkId: 'c1', pass: true }],
        assistantText: 'answer', nextState: { version: 3, value: 3 }, isContinue: false, ...overrides,
    };
}

it('rejects stale head revisions before hashing, mutation, or save', async () => {
    const { store, current, saveChat, context } = setup();
    const result = await store.commitSegment(commitInput(current, { expectedHeadRevision: 6 }));
    expect(result).toEqual({ ok: false, reason: 'head-conflict' });
    expect(saveChat).not.toHaveBeenCalled();
    expect(context.chatMetadata.dualModelEngine.headRevision).toBe(7);
});

it('rejects ABA state versions even when the head revision matches', async () => {
    const { store, current, saveChat } = setup({ metadata: envelope({ stateVersion: 18, headRevision: 9 }) });
    const result = await store.commitSegment(commitInput(current, { expectedHeadRevision: 9, baseStateVersion: 10, nextState: { version: 11, value: 11 } }));
    expect(result).toEqual({ ok: false, reason: 'state-conflict' });
    expect(saveChat).not.toHaveBeenCalled();
});

it('persists a new branch from its base snapshot, clones caller data, and increments revision once', async () => {
    const { store, current, context, saveChat } = setup();
    const input = commitInput(current);
    const result = await store.commitSegment(input);
    expect(result).toMatchObject({ ok: true, stateVersion: 3, headRevision: 8 });
    const branch = current.swipe_info[0].extra.dualModelEngine.branch;
    expect(branch).toMatchObject({ branchId: 'b1', baseStateVersion: 2, baseSnapshot: { version: 2, value: 2 }, status: 'committed' });
    expect(context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'm1', swipeId: 0, branchId: 'b1' });
    input.patch.operations.push({ op: 'add' });
    input.checks[0].pass = false;
    input.nextState.value = 99;
    input.baseSnapshot.value = 99;
    expect(branch.segments[0]).toMatchObject({ patch: { operations: [] }, checks: [{ checkId: 'c1', pass: true }], postSnapshot: { version: 3, value: 3 } });
    expect(branch.baseSnapshot.value).toBe(2);
    expect(saveChat).toHaveBeenCalledTimes(1);
});

it('does not mutate after a delayed hash when its commit signal is aborted', async () => {
    let resolveHash; const hashText = vi.fn(() => new Promise(resolve => { resolveHash = resolve; })); const { context, current, saveChat } = setup(); const store = createStateStore({ adapter: { getContext: () => context, saveChat }, makeId: () => 'generated', hashText }); const controller = new AbortController(); const before = structuredClone(context);
    const pending = store.commitSegment(commitInput(current, { signal: controller.signal })); await vi.waitFor(() => expect(hashText).toHaveBeenCalledOnce()); controller.abort(); resolveHash('hash:answer');
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' }); expect(context).toEqual(before); expect(saveChat).not.toHaveBeenCalled();
});

it('marks only the still-matching branch failed transactionally', async () => {
    const { store, current, context, saveChat } = setup();
    await store.commitSegment(commitInput(current));
    await expect(store.markBranchFailed({ chatId: 'chat-a', messageId: 'm1', swipeId: 0, branchId: 'b1', requestId: 'r2' })).resolves.toEqual({ ok: true });
    expect(current.swipe_info[0].extra.dualModelEngine.branch.status).toBe('stale');
    expect(context.chatMetadata.dualModelEngine.taskStatus).toEqual({ state: 'failed', requestId: 'r2' });
    expect(saveChat).toHaveBeenCalledTimes(2);
    await expect(store.markBranchFailed({ chatId: 'chat-a', messageId: 'm1', swipeId: 0, branchId: 'other', requestId: 'r3' })).resolves.toEqual({ ok: false, reason: 'branch-conflict' });
});

it('rolls back a newly-created failed branch when saving fails', async () => {
    const saveChat = vi.fn().mockRejectedValue(new Error('disk'));
    const { store, current, context } = setup({ saveChat });
    const before = structuredClone({ metadata: context.chatMetadata, extra: current.extra, swipes: current.swipe_info });
    await expect(store.markBranchFailed({ chatId: 'chat-a', messageId: 'm1', swipeId: 0, branchId: 'new', requestId: 'r', baseSnapshot: { version: 2, value: 2 }, baseStateVersion: 2, isContinue: false })).resolves.toMatchObject({ ok: false, reason: 'save-failed' });
    expect({ metadata: context.chatMetadata, extra: current.extra, swipes: current.swipe_info }).toEqual(before);
});

it('rolls back a replaced cloned source branch when saving fails', async () => {
    const saveChat = vi.fn().mockRejectedValue(new Error('disk'));
    const source = { branchId: 'source', baseStateVersion: 1, baseSnapshot: { version: 1 }, segments: [], status: 'committed' };
    const { store, current, context } = setup({ current: message(source), saveChat });
    const before = structuredClone({ metadata: context.chatMetadata, extra: current.extra, swipes: current.swipe_info });
    await store.markBranchFailed({ chatId: 'chat-a', messageId: 'm1', swipeId: 0, branchId: 'dest', requestId: 'r', baseSnapshot: { version: 1 }, baseStateVersion: 1, baseBranchId: 'source', isContinue: false });
    expect({ metadata: context.chatMetadata, extra: current.extra, swipes: current.swipe_info }).toEqual(before);
});

it('T1 creates a pinned envelope once when one is missing', async () => {
    const { store, context, saveChat } = setup({ metadata: null });
    const result = await store.ensureEnvelope({ presetId: 'narrative', initialState: { version: 0, value: 0 } });
    expect(result).toMatchObject({ ok: true, created: true, value: { preset: { id: 'narrative', version: 1 } } });
    expect(saveChat).toHaveBeenCalledOnce(); expect(context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 0, value: 0 });
});

it('T2 rolls back envelope creation when save rejects', async () => {
    const { store, context } = setup({ metadata: null, saveChat: vi.fn().mockRejectedValue(new Error('no')) });
    await expect(store.ensureEnvelope({ presetId: 'narrative', initialState: { version: 0 } })).resolves.toMatchObject({ ok: false, reason: 'save-failed' });
    expect(context.chatMetadata).toEqual({ dualModelEngine: null });
});

it('T4 prepares a cloned source branch and T5 rejects missing source', () => {
    const source = { branchId: 'source', baseStateVersion: 1, baseSnapshot: { version: 1, nested: { x: 1 } }, segments: [], status: 'committed' };
    const { store, current } = setup({ current: message(source) });
    const prepared = store.prepareSwipeGeneration({ target: current });
    expect(prepared).toMatchObject({ ok: true, baseStateVersion: 1, baseBranchId: 'source' }); prepared.baseSnapshot.nested.x = 2;
    expect(source.baseSnapshot.nested.x).toBe(1);
    expect(store.prepareSwipeGeneration({ target: message() })).toEqual({ ok: false, reason: 'missing-source-branch' });
});

it('rejects a failed destination when the host selected an unrelated branch', async () => {
    const other = { branchId: 'other', baseStateVersion: 0, baseSnapshot: { version: 0 }, segments: [], status: 'committed' };
    const { store, current, context, saveChat } = setup({ current: message(other) }); const before = structuredClone(context.chatMetadata);
    await expect(store.markBranchFailed({ chatId: 'chat-a', messageId: 'm1', swipeId: 0, branchId: 'dest', requestId: 'r', baseSnapshot: { version: 2 }, baseStateVersion: 2, isContinue: false })).resolves.toEqual({ ok: false, reason: 'branch-conflict' });
    expect(current.swipe_info[0].extra.dualModelEngine.branch).toEqual(other); expect(context.chatMetadata).toEqual(before); expect(saveChat).not.toHaveBeenCalled();
});

it('rejects duplicate requests before hashing or save', async () => {
    const { store, current, saveChat } = setup({ metadata: envelope({ lastCommittedRequestId: 'r1' }) });
    expect(await store.commitSegment(commitInput(current))).toEqual({ ok: false, reason: 'duplicate-request' });
    expect(saveChat).not.toHaveBeenCalled();
});

it('rejects changed continue branch identity before hash or save', async () => {
    const branch = { branchId: 'original', baseStateVersion: 2, segments: [] };
    const { store, current, saveChat } = setup({ current: message(branch) });
    expect(await store.commitSegment(commitInput(current, { isContinue: true }))).toEqual({ ok: false, reason: 'branch-conflict' });
    expect(saveChat).not.toHaveBeenCalled();
});

it('replaces a host-cloned branch for a new non-continue commit', async () => {
    const branch = { branchId: 'copied', baseStateVersion: 1, baseSnapshot: { version: 1 }, segments: [{ requestId: 'old' }] };
    const { store, current } = setup({ current: message(branch) });
    await expect(store.commitSegment(commitInput(current))).resolves.toMatchObject({ ok: true });
    expect(current.swipe_info[0].extra.dualModelEngine.branch).toMatchObject({ branchId: 'b1', baseStateVersion: 2, segments: [{ requestId: 'r1' }] });
});

it('rolls back metadata, current namespace, and swipe namespace when saving a commit fails', async () => {
    const saveChat = vi.fn().mockRejectedValue(new Error('disk full'));
    const { store, current, context } = setup({ saveChat });
    const beforeMetadata = structuredClone(context.chatMetadata.dualModelEngine);
    const beforeExtra = structuredClone(current.extra);
    const beforeSwipe = structuredClone(current.swipe_info[0]);
    const result = await store.commitSegment(commitInput(current));
    expect(result).toMatchObject({ ok: false, reason: 'save-failed' });
    expect(context.chatMetadata.dualModelEngine).toEqual(beforeMetadata);
    expect(current.extra).toEqual(beforeExtra);
    expect(current.swipe_info[0]).toEqual(beforeSwipe);
    expect(saveChat).toHaveBeenCalledTimes(1);
});

it('rolls back without saving when hashing fails', async () => {
    const { context, current, saveChat } = setup();
    const store = createStateStore({ adapter: { getContext: () => context, saveChat }, makeId: () => 'generated', hashText: async () => { throw new Error('hash failed'); } });
    const before = structuredClone(context);
    expect(await store.commitSegment(commitInput(current))).toMatchObject({ ok: false, reason: 'hash-failed' });
    expect(context).toEqual(before);
    expect(saveChat).not.toHaveBeenCalled();
});

it('restores the last continue segment and increments revision once', async () => {
    const branch = { branchId: 'b1', status: 'committed', segments: [{ postSnapshot: { version: 2, value: 2 } }, { postSnapshot: { version: 3, value: 3 } }] };
    const { store, current, context, saveChat } = setup({ current: message(branch) });
    await expect(store.restoreBranch(current, 0)).resolves.toEqual({ ok: true, snapshot: { version: 3, value: 3 } });
    expect(context.chatMetadata.dualModelEngine).toMatchObject({ stateVersion: 3, headRevision: 8, activeRef: { messageId: 'm1', swipeId: 0, branchId: 'b1' } });
    expect(saveChat).toHaveBeenCalledTimes(1);
});

it('rolls back restore when save fails and reports missing state safely', async () => {
    const branch = { branchId: 'b1', segments: [{ postSnapshot: { version: 3 } }] };
    const saveChat = vi.fn().mockRejectedValue(new Error('disk full'));
    const { store, current, context } = setup({ current: message(branch), saveChat });
    const before = structuredClone(context);
    expect(await store.restoreBranch(current, 0)).toMatchObject({ ok: false, reason: 'save-failed' });
    expect(context).toEqual(before);
    const absent = message();
    context.chat = [absent];
    expect(await store.restoreBranch(absent, 0)).toEqual({ ok: false, reason: 'missing-snapshot' });
});

it('marks only descendant branches stale, increments revision once, and rolls back save failures', async () => {
    const currentBranch = { branchId: 'current', status: 'committed', segments: [] };
    const laterBranch = { branchId: 'later', status: 'committed', segments: [] };
    const { store, current, context, saveChat } = setup({ current: message(currentBranch) });
    const later = message(laterBranch);
    context.chat = [{ is_user: true }, current, later];
    await expect(store.markStaleAfter(1)).resolves.toEqual({ ok: true });
    expect(currentBranch.status).toBe('committed');
    expect(laterBranch.status).toBe('stale');
    expect(later.extra.dualModelEngine.branch.status).toBe('stale');
    expect(context.chatMetadata.dualModelEngine.headRevision).toBe(8);
    expect(saveChat).toHaveBeenCalledTimes(1);

    const badSave = vi.fn().mockRejectedValue(new Error('disk full'));
    const bad = setup({ current: message(currentBranch), saveChat: badSave });
    const descendant = message(laterBranch);
    bad.context.chat = [bad.current, descendant];
    const before = structuredClone(bad.context);
    expect(await bad.store.markStaleAfter(0)).toMatchObject({ ok: false, reason: 'save-failed' });
    expect(bad.context).toEqual(before);
});

it('lists deduplicated cloned rule records and returns missing context as a result', () => {
    const first = { branchId: 'a', segments: [{ checks: [{ checkId: 'same', verdict: 'old' }] }] };
    const second = { branchId: 'b', segments: [{ checks: [{ checkId: 'same', verdict: 'new' }, { checkId: 'other' }] }] };
    const { store, context } = setup({ current: message(first) });
    context.chat.push(message(second));
    const records = store.listRuleRecords();
    expect(records).toEqual([{ checkId: 'same', verdict: 'new' }, { checkId: 'other' }]);
    records[0].verdict = 'mutated';
    expect(store.listRuleRecords()[0].verdict).toBe('new');
    context.chatMetadata = {};
    expect(store.loadEnvelope()).toEqual({ ok: false, reason: 'missing-envelope' });
});

it('rejects an old revision after real restore transactions advance 18-to-10-to-18', async () => {
    const branch = { branchId: 'b1', segments: [{ postSnapshot: { version: 10 } }] };
    const hashText = vi.fn(async text => `hash:${text}`);
    const { current, context, saveChat } = setup({ current: message(branch), metadata: envelope({ stateVersion: 18, headRevision: 7 }) });
    const store = createStateStore({ adapter: { getContext: () => context, saveChat }, makeId: () => 'generated', hashText });
    await expect(store.restoreBranch(current, 0)).resolves.toMatchObject({ ok: true, snapshot: { version: 10 } });
    current.swipe_info[0].extra.dualModelEngine.branch.segments.push({ postSnapshot: { version: 18 } });
    await expect(store.restoreBranch(current, 0)).resolves.toMatchObject({ ok: true, snapshot: { version: 18 } });
    expect(context.chatMetadata.dualModelEngine).toMatchObject({ stateVersion: 18, headRevision: 9 });
    expect(await store.commitSegment(commitInput(current, {
        baseStateVersion: 18, expectedHeadRevision: 7, nextState: { version: 19, value: 19 },
    }))).toEqual({ ok: false, reason: 'head-conflict' });
    expect(hashText).not.toHaveBeenCalled();
    expect(saveChat).toHaveBeenCalledTimes(2);
});

it('revalidates after a delayed hash and rejects a changed revision without saving', async () => {
    let resolveHash;
    const hashText = vi.fn(() => new Promise(resolve => { resolveHash = resolve; }));
    const { context, current, saveChat } = setup();
    const store = createStateStore({ adapter: { getContext: () => context, saveChat }, makeId: () => 'generated', hashText });
    const pending = store.commitSegment(commitInput(current));
    context.chatMetadata.dualModelEngine.headRevision = 8;
    resolveHash('hash:answer');
    expect(await pending).toEqual({ ok: false, reason: 'head-conflict' });
    expect(hashText).toHaveBeenCalledTimes(1);
    expect(saveChat).not.toHaveBeenCalled();
});

it('binds a delayed commit to its captured chat array even if the same message is retained', async () => {
    let resolveHash;
    const hashText = vi.fn(() => new Promise(resolve => { resolveHash = resolve; }));
    const { context, current, saveChat } = setup();
    const store = createStateStore({ adapter: { getContext: () => ({ ...context }), saveChat }, makeId: () => 'generated', hashText });
    const before = structuredClone(context);
    const pending = store.commitSegment(commitInput(current));
    context.chat = [current];
    resolveHash('hash:answer');
    expect(await pending).toEqual({ ok: false, reason: 'stale-chat' });
    expect(hashText).toHaveBeenCalledTimes(1);
    expect(saveChat).not.toHaveBeenCalled();
    expect(context).toEqual(before);
});

it('allows a normal commit when getContext returns fresh wrappers around the same chat array', async () => {
    const { context, current, saveChat } = setup();
    const store = createStateStore({ adapter: { getContext: () => ({ ...context }), saveChat }, makeId: () => 'generated', hashText: async text => `hash:${text}` });
    await expect(store.commitSegment(commitInput(current))).resolves.toMatchObject({ ok: true });
    expect(saveChat).toHaveBeenCalledTimes(1);
});

it('restores every swipe identity when a multi-swipe commit save fails', async () => {
    const saveChat = vi.fn().mockRejectedValue(new Error('disk full'));
    const { store, current, context } = setup({ saveChat });
    current.swipe_info.push({ extra: { dualModelEngine: { branch: { branchId: 'copied', segments: [] } } } });
    const before = structuredClone(context);
    expect(await store.commitSegment(commitInput(current))).toMatchObject({ ok: false, reason: 'save-failed' });
    expect(context).toEqual(before);
});

it('restores a multi-swipe descendant and its selected mirror exactly when invalidation save fails', async () => {
    const first = { branchId: 'first', status: 'committed', segments: [] };
    const second = { branchId: 'second', status: 'committed', segments: [] };
    const saveChat = vi.fn().mockRejectedValue(new Error('disk full'));
    const { store, context } = setup({ saveChat });
    const later = message(first, 1);
    later.swipe_info.push({ extra: { dualModelEngine: { messageId: 'm1', branch: second } } });
    later.extra.dualModelEngine = structuredClone(later.swipe_info[1].extra.dualModelEngine);
    context.chat.push(later);
    const before = structuredClone(context);
    expect(await store.markStaleAfter(0)).toMatchObject({ ok: false, reason: 'save-failed' });
    expect(context).toEqual(before);
});

it('rejects malformed envelopes and restore identities before mutation or save', async () => {
    const { store, current, context, saveChat } = setup({ metadata: envelope({ headRevision: Number.NaN }) });
    expect(await store.commitSegment(commitInput(current))).toEqual({ ok: false, reason: 'invalid-envelope' });
    expect(await store.markStaleAfter(0)).toEqual({ ok: false, reason: 'invalid-envelope' });
    context.chatMetadata.dualModelEngine = envelope();
    const branch = { branchId: '', segments: [{ postSnapshot: { version: 3 } }] };
    current.swipe_info[0].extra.dualModelEngine = { messageId: '', branch };
    expect(await store.restoreBranch(current, 0)).toEqual({ ok: false, reason: 'invalid-identity' });
    expect(saveChat).not.toHaveBeenCalled();
});

it('returns invalid input without mutating when ensureBranch receives an invalid swipe index', () => {
    const { store, current } = setup();
    const before = structuredClone(current);
    expect(() => store.ensureBranch(current, -1, { version: 2 }, 2, 'b1')).toThrow('Invalid swipe index');
    expect(current).toEqual(before);
});
