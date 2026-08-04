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
