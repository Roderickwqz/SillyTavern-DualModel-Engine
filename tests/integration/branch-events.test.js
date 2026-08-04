import { expect, it, vi } from 'vitest';
import { createRollbackManager } from '../../src/rollback-manager.js';
import { createStateStore } from '../../src/state-store.js';
import { createChatTaskQueue } from '../../src/task-queue.js';

function branch(id, version, status = 'committed') {
    return { branchId: id, baseStateVersion: version - 1, baseSnapshot: { version: version - 1 }, status, segments: [{ postSnapshot: { version }, assistantTextHash: `hash:answer-${id}` }] };
}

function assistant(id, swipes, selected = 0) {
    return {
        mes: `answer-${id}`, swipe_id: selected,
        extra: { dualModelEngine: { messageId: id, branch: structuredClone(swipes[selected]) } },
        swipe_info: swipes.map(value => ({ extra: { dualModelEngine: { messageId: id, branch: structuredClone(value) } } })),
    };
}

function eventHost({ chat = [], chatId = 'chat-a', writable = true, confirm, replayTurn, on, off, autoBind = true } = {}) {
    const listeners = new Map();
    const context = {
        chatId, chat, chatMetadata: { dualModelEngine: {
            schemaVersion: 1, stateVersion: 0, headRevision: 0, initialSnapshot: { version: 0 }, activeSnapshot: { version: 0 }, activeRef: null,
            preset: { id: 'narrative', version: 1 }, taskStatus: { state: 'idle', requestId: null }, lastCommittedRequestId: null,
        } },
    };
    const saveChat = vi.fn(async () => {});
    const adapter = {
        events: { MESSAGE_SWIPED: 'swiped', MESSAGE_SWIPE_DELETED: 'swipe-deleted', MESSAGE_EDITED: 'edited', MESSAGE_DELETED: 'deleted', CHAT_CHANGED: 'chat-changed' },
        getContext: () => context, saveChat,
        on: on ?? ((name, fn) => listeners.set(name, fn)), off: off ?? ((name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); }),
    };
    const store = createStateStore({ adapter, hashText: async text => `hash:${text}` });
    const queue = createChatTaskQueue();
    const manager = createRollbackManager({ adapter, store, queue, confirm, replayTurn, isWritable: () => writable });
    if (autoBind) manager.bind();
    return { context, saveChat, adapter, store, queue, manager, emit: (name, payload) => listeners.get(name)?.(payload) };
}

it('replays visible selected assistant branches sequentially and stops at the first failure', async () => {
    const chat = [{ is_user: true }, { swipe_id: 0 }, { is_system: true }, { swipe_id: 1 }, { swipe_id: 0 }];
    const replayTurn = vi.fn(async ({ messageIndex, baseSnapshot }) => messageIndex === 4
        ? { ok: false }
        : { ok: true, snapshot: { version: baseSnapshot.version + 1 }, stateVersion: baseSnapshot.version + 1 });
    const manager = createRollbackManager({
        adapter: { getContext: () => ({ chatId: 'chat', chat }) }, queue: { enqueue: (_chat, _name, fn) => fn({ throwIfAborted() {} }) },
        store: { findLastValidSnapshot: () => ({ snapshot: { version: 2 } }) }, replayTurn,
    });
    await expect(manager.recalculate(1)).resolves.toEqual({ ok: false, failedAt: 4, lastValidVersion: 4 });
    expect(replayTurn.mock.calls.map(([input]) => input.messageIndex)).toEqual([1, 3, 4]);
    expect(replayTurn.mock.calls[1][0].baseSnapshot).toEqual({ version: 3 });
});

it('C1 MESSAGE_SWIPED restores the selected persisted final snapshot through the real store', async () => {
    const first = branch('first', 1); const selected = branch('selected', 4);
    const message = assistant('m1', [first, selected]); const host = eventHost({ chat: [{ is_user: true }, message] });
    message.swipe_id = 1;
    await host.emit('swiped', 1);
    await host.queue.waitForIdle('chat-a');
    expect(host.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 4 });
    expect(host.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'm1', swipeId: 1, branchId: 'selected' });
    expect(host.saveChat).toHaveBeenCalledOnce();
});

it('C2 rapid host-cloned 0 to 1 to 0 records the original source without an empty restore', async () => {
    const source = branch('source', 2); const message = assistant('m1', [source, source]);
    const host = eventHost({ chat: [message] });
    message.swipe_id = 1;
    expect(host.emit('swiped', 0)).toMatchObject({ ok: true, pendingGeneration: true, sourceSwipeId: 0 });
    message.swipe_id = 0;
    await host.emit('swiped', 0);
    await host.queue.waitForIdle('chat-a');
    expect(host.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 2 });
    expect(host.saveChat).toHaveBeenCalledOnce();
    expect(host.manager.prepareSwipeGeneration(0, 'swipe')).toMatchObject({ ok: true, baseBranchId: 'source', baseSnapshot: { version: 1 }, reusableChecks: [] });
});

it('C4 MESSAGE_SWIPE_DELETED uses post-splice current selection for a non-current swipe', async () => {
    const old = branch('old', 1); const selected = branch('selected', 3);
    const message = assistant('m1', [old, selected], 1); const host = eventHost({ chat: [message] });
    Object.assign(host.context.chatMetadata.dualModelEngine, { stateVersion: 3, activeSnapshot: { version: 3 }, activeRef: { messageId: 'm1', swipeId: 1, branchId: 'selected' } });
    message.swipe_info.splice(0, 1); message.swipe_id = 0;
    await host.emit('swipe-deleted', { messageId: 0, swipeId: 0 });
    await host.queue.waitForIdle('chat-a');
    expect(message.swipe_info[0].extra.dualModelEngine.branch.branchId).toBe('selected');
    expect(host.context.chatMetadata.dualModelEngine.headRevision).toBe(1);
    expect(host.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'm1', swipeId: 0, branchId: 'selected' });
});

it('C4 MESSAGE_SWIPE_DELETED restores the surviving current swipe after its old slot is spliced', async () => {
    const old = branch('old', 1); const selected = branch('selected', 3);
    const current = assistant('m2', [old, selected], 1); const currentHost = eventHost({ chat: [current] });
    Object.assign(currentHost.context.chatMetadata.dualModelEngine, { stateVersion: 3, activeSnapshot: { version: 3 }, activeRef: { messageId: 'm2', swipeId: 1, branchId: 'selected' } });
    current.swipe_info.splice(1, 1); current.swipe_id = 0;
    await currentHost.emit('swipe-deleted', { messageId: 0, swipeId: 1, newSwipeId: 0 });
    await currentHost.queue.waitForIdle('chat-a');
    expect(currentHost.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'm2', swipeId: 0, branchId: 'old' });
    expect(currentHost.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 1 });
});

it('C4 MESSAGE_SWIPE_DELETED leaves a later active head alone for historical non-current and current swipes', async () => {
    const historical = assistant('history', [branch('old', 1), branch('history', 2)], 1);
    const tail = assistant('tail', [branch('tail', 4)]);
    const host = eventHost({ chat: [historical, tail] });
    Object.assign(host.context.chatMetadata.dualModelEngine, { stateVersion: 4, activeSnapshot: { version: 4 }, activeRef: { messageId: 'tail', swipeId: 0, branchId: 'tail' } });

    historical.swipe_info.splice(0, 1); historical.swipe_id = 0;
    await host.emit('swipe-deleted', { messageId: 0, swipeId: 0, newSwipeId: 0 });
    await host.queue.waitForIdle('chat-a');
    expect(host.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'tail', swipeId: 0, branchId: 'tail' });
    expect(host.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 4 });

    const current = assistant('history-2', [branch('old-2', 1), branch('history-2', 2)], 1);
    const currentHost = eventHost({ chat: [current, tail] });
    Object.assign(currentHost.context.chatMetadata.dualModelEngine, { stateVersion: 4, activeSnapshot: { version: 4 }, activeRef: { messageId: 'tail', swipeId: 0, branchId: 'tail' } });
    current.swipe_info.splice(1, 1); current.swipe_id = 0;
    await currentHost.emit('swipe-deleted', { messageId: 0, swipeId: 1, newSwipeId: 0 });
    await currentHost.queue.waitForIdle('chat-a');
    expect(currentHost.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'tail', swipeId: 0, branchId: 'tail' });
    expect(currentHost.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 4 });
});

it('C5 MESSAGE_DELETED finds the changed boundary from post-mutation chat state', async () => {
    const first = assistant('first', [branch('first', 1)]);
    const last = assistant('last', [branch('last', 2)]);
    const host = eventHost({ chat: [{ is_user: true }, first, { is_user: true }, last] });
    Object.assign(host.context.chatMetadata.dualModelEngine, { stateVersion: 2, activeSnapshot: { version: 2 }, activeRef: { messageId: 'last', swipeId: 0, branchId: 'last' } });

    host.context.chat.splice(1, 1);
    await host.emit('deleted', host.context.chat.length);
    await host.queue.waitForIdle('chat-a');

    expect(last.swipe_info[0].extra.dualModelEngine.branch.status).toBe('stale');
    expect(host.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 0 });
});

it('C5 MESSAGE_DELETED invalidates surviving descendants even when its active reference was deleted', async () => {
    const deleted = assistant('deleted', [branch('deleted', 1)]);
    const descendant = assistant('descendant', [branch('descendant', 2)]);
    const host = eventHost({ chat: [{ is_user: true }, deleted, descendant] });
    Object.assign(host.context.chatMetadata.dualModelEngine, { stateVersion: 1, activeSnapshot: { version: 1 }, activeRef: { messageId: 'deleted', swipeId: 0, branchId: 'deleted' } });

    host.context.chat.splice(1, 1);
    await host.emit('deleted', host.context.chat.length);
    await host.queue.waitForIdle('chat-a');

    expect(descendant.swipe_info[0].extra.dualModelEngine.branch.status).toBe('stale');
    expect(host.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 0 });
});

it('C5 MESSAGE_DELETED restores the preceding selected branch after tail truncation', async () => {
    const first = assistant('first', [branch('first', 1)]);
    const tail = assistant('tail', [branch('tail', 2)]);
    const host = eventHost({ chat: [{ is_user: true }, first, tail] });
    Object.assign(host.context.chatMetadata.dualModelEngine, { stateVersion: 2, activeSnapshot: { version: 2 }, activeRef: { messageId: 'tail', swipeId: 0, branchId: 'tail' } });

    host.context.chat.splice(2, 1);
    await host.emit('deleted', host.context.chat.length);
    await host.queue.waitForIdle('chat-a');

    expect(host.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 1 });
    expect(host.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'first', swipeId: 0, branchId: 'first' });
});

it('C5 MESSAGE_DELETED uses the earliest boundary after a combined host deletion', async () => {
    const first = assistant('first', [branch('first', 1)]);
    const middle = assistant('middle', [branch('middle', 2)]);
    const survivor = assistant('survivor', [branch('survivor', 3)]);
    const host = eventHost({ chat: [{ is_user: true }, first, middle, survivor] });
    Object.assign(host.context.chatMetadata.dualModelEngine, { stateVersion: 3, activeSnapshot: { version: 3 }, activeRef: { messageId: 'survivor', swipeId: 0, branchId: 'survivor' } });

    host.context.chat.splice(1, 2);
    await host.emit('deleted', host.context.chat.length);
    await host.queue.waitForIdle('chat-a');

    expect(survivor.swipe_info[0].extra.dualModelEngine.branch.status).toBe('stale');
    expect(host.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 0 });
});

it('C5 regenerate suppression does not swallow a same-chat deletion of a different message', async () => {
    const other = assistant('other', [branch('other', 1)]);
    const target = assistant('target', [branch('target', 2)]);
    const host = eventHost({ chat: [other, target] });
    Object.assign(host.context.chatMetadata.dualModelEngine, { stateVersion: 2, activeSnapshot: { version: 2 }, activeRef: { messageId: 'target', swipeId: 0, branchId: 'target' } });
    expect(host.manager.prepareSwipeGeneration(1, 'regenerate')).toMatchObject({ ok: true });

    host.context.chat.splice(0, 1);
    await host.emit('deleted', host.context.chat.length);
    await host.queue.waitForIdle('chat-a');

    expect(target.swipe_info[0].extra.dualModelEngine.branch.status).toBe('stale');
    expect(host.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 0 });
});

it('C4 rejects malformed swipe deletion events without persisting', async () => {
    const host = eventHost({ chat: [assistant('m1', [branch('only', 1)])] });
    expect(host.emit('swipe-deleted', { messageId: '0', swipeId: 0 })).toEqual({ ok: false, reason: 'invalid-swipe-delete' });
    expect(host.saveChat).not.toHaveBeenCalled();
});

it('C12 bound event handlers are idempotent and do not mutate read-only chats', async () => {
    const message = assistant('m1', [branch('zero', 1), branch('one', 2)]); const host = eventHost({ chat: [message], writable: false });
    host.manager.bind();
    message.swipe_id = 1;
    expect(host.emit('swiped', 0)).toEqual({ ok: false, reason: 'read-only' });
    await expect(host.emit('edited', 0)).resolves.toEqual({ ok: false, reason: 'read-only' });
    await expect(host.emit('deleted', 0)).resolves.toEqual({ ok: false, reason: 'read-only' });
    expect(host.emit('swipe-deleted', { messageId: 0, swipeId: 0 })).toEqual({ ok: false, reason: 'read-only' });
    expect(host.saveChat).not.toHaveBeenCalled();
    host.manager.destroy(); host.manager.destroy();
    expect(host.emit('swiped', 0)).toBeUndefined();
});

it('C6 MESSAGE_EDITED invalidates from the next message for a user edit, then keeps the last valid boundary when confirmation is declined', async () => {
    const prior = assistant('prior', [branch('prior', 1)]);
    const affected = assistant('affected', [branch('affected', 2)]);
    const host = eventHost({ chat: [prior, { is_user: true, mes: 'edited user' }, affected], confirm: async () => false });
    Object.assign(host.context.chatMetadata.dualModelEngine, { stateVersion: 2, activeSnapshot: { version: 2 }, activeRef: { messageId: 'affected', swipeId: 0, branchId: 'affected' } });

    await host.emit('edited', 1);
    await host.queue.waitForIdle('chat-a');

    expect(affected.swipe_info[0].extra.dualModelEngine.branch.status).toBe('stale');
    expect(host.context.chatMetadata.dualModelEngine.activeSnapshot).toEqual({ version: 1 });
    expect(host.context.chatMetadata.dualModelEngine.activeRef).toEqual({ messageId: 'prior', swipeId: 0, branchId: 'prior' });
    expect(host.saveChat).toHaveBeenCalledOnce();
});

it('C6 selected assistant edit stales its selected branch and descendants, then replays visible selected assistants in order', async () => {
    const selected = assistant('selected', [branch('old', 2), branch('selected', 2)], 1);
    const descendant = assistant('descendant', [branch('descendant', 3)]);
    const calls = [];
    const host = eventHost({ chat: [{ is_user: true }, selected, descendant], confirm: async () => true, replayTurn: async input => {
        calls.push(input); return { ok: true, snapshot: { version: input.baseSnapshot.version + 1 }, stateVersion: input.baseSnapshot.version + 1 };
    } });

    await host.emit('edited', 1);
    await host.queue.waitForIdle('chat-a');

    expect(selected.swipe_info[0].extra.dualModelEngine.branch.status).toBe('committed');
    expect(selected.swipe_info[1].extra.dualModelEngine.branch.status).toBe('stale');
    expect(descendant.swipe_info[0].extra.dualModelEngine.branch.status).toBe('stale');
    expect(calls.map(call => call.messageIndex)).toEqual([1, 2]);
    expect(calls.map(call => call.baseSnapshot.version)).toEqual([0, 1]);
    expect(host.saveChat).toHaveBeenCalledOnce();
});

it('C10 turns confirmation and replay exceptions into contained failed recalculation results', async () => {
    const message = assistant('target', [branch('target', 1)]);
    const confirmation = eventHost({ chat: [message], confirm: async () => { throw new Error('dialog failed'); } });
    await expect(confirmation.manager.invalidateForEdit(0)).resolves.toMatchObject({ ok: false, reason: 'confirmation-failed' });
    await confirmation.queue.waitForIdle('chat-a');
    expect(confirmation.saveChat).toHaveBeenCalledOnce();

    const replay = eventHost({ chat: [assistant('target', [branch('target', 1)])], confirm: async () => true, replayTurn: async () => { throw new Error('model failed'); } });
    await expect(replay.manager.invalidateForEdit(0)).resolves.toMatchObject({ ok: false, reason: 'replay-failed', failedAt: 0, lastValidVersion: 0 });
    await replay.queue.waitForIdle('chat-a');
    expect(replay.saveChat).toHaveBeenCalledOnce();
});

it('C11 rejects an edited event whose captured message was replaced before its queued task begins', async () => {
    const first = assistant('first', [branch('first', 1)]); const target = assistant('target', [branch('target', 2)]);
    const host = eventHost({ chat: [first, target], confirm: async () => false });
    const blocker = host.queue.enqueue('chat-a', 'blocker', async () => {});
    const queued = host.emit('edited', 1);
    host.context.chat[1] = assistant('replacement', [branch('replacement', 9)]);
    await blocker;
    await expect(queued).resolves.toEqual({ ok: false, reason: 'stale-message' });
    expect(host.saveChat).not.toHaveBeenCalled();
});

it('C10 stops a cancelled recalculation after an abort-ignoring replay resolves and does not start its successor', async () => {
    let resolveReplay; const calls = [];
    const queue = createChatTaskQueue(); const chat = [assistant('first', [branch('first', 1)])];
    const manager = createRollbackManager({
        adapter: { getContext: () => ({ chatId: 'chat-a', chat }) }, queue,
        store: { findLastValidSnapshot: () => ({ snapshot: { version: 0 } }) },
        replayTurn: input => { calls.push(input); return new Promise(resolve => { resolveReplay = resolve; }); },
    });
    const result = manager.recalculate(0);
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    queue.cancelChat('chat-a', 'cancelled');
    resolveReplay({ ok: true, snapshot: { version: 1 }, stateVersion: 1 });
    await expect(result).rejects.toMatchObject({ name: 'AbortError' });
    expect(calls).toHaveLength(1);
});

it('C11 queued swipe restoration cannot revive a branch made stale by an earlier edit', async () => {
    const message = assistant('target', [branch('zero', 1), branch('one', 2)], 0);
    const host = eventHost({ chat: [message], confirm: async () => false });
    const blocker = host.queue.enqueue('chat-a', 'blocker', async () => {});
    const edit = host.emit('edited', 0);
    message.swipe_id = 1;
    const restore = host.emit('swiped', 0);
    await blocker;
    await edit;
    await expect(restore).resolves.toEqual({ ok: false, reason: 'stale-branch' });
    expect(message.swipe_info[1].extra.dualModelEngine.branch.status).toBe('stale');
    expect(host.context.chatMetadata.dualModelEngine.activeRef).toBeNull();
});

it('C12 refreshes selected swipes on CHAT_CHANGED and rolls back partial bind registrations when adapter.on throws', () => {
    const listeners = new Map(); let count = 0;
    const host = eventHost({ autoBind: false, chat: [assistant('one', [branch('one', 1), branch('one-alt', 2)])], on: (name, fn) => { count += 1; if (count === 2) throw new Error('registration failed'); listeners.set(name, fn); }, off: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); } });
    expect(() => host.manager.bind()).toThrow('registration failed');
    expect(listeners.size).toBe(0);
    host.adapter.on = (name, fn) => listeners.set(name, fn);
    host.manager.bind();
    expect(listeners.size).toBe(5);
    host.context.chat[0].swipe_id = 1;
    host.emit('chat-changed');
    expect(host.manager.prepareSwipeGeneration(0, 'swipe')).toMatchObject({ baseSwipeId: 1, baseBranchId: 'one-alt' });
    host.manager.destroy();
    expect(host.emit('edited', 0)).toBeUndefined();
    expect(host.emit('deleted', 0)).toBeUndefined();
    expect(host.emit('swiped', 0)).toBeUndefined();
    expect(host.emit('swipe-deleted', { messageId: 0, swipeId: 0 })).toBeUndefined();
    expect(host.emit('chat-changed')).toBeUndefined();
});
