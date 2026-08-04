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

function eventHost({ chat = [], chatId = 'chat-a', writable = true } = {}) {
    const listeners = new Map();
    const context = {
        chatId, chat, chatMetadata: { dualModelEngine: {
            schemaVersion: 1, stateVersion: 0, headRevision: 0, initialSnapshot: { version: 0 }, activeSnapshot: { version: 0 }, activeRef: null,
            preset: { id: 'narrative', version: 1 }, taskStatus: { state: 'idle', requestId: null }, lastCommittedRequestId: null,
        } },
    };
    const saveChat = vi.fn(async () => {});
    const adapter = {
        events: { MESSAGE_SWIPED: 'swiped', MESSAGE_SWIPE_DELETED: 'swipe-deleted', MESSAGE_EDITED: 'edited', MESSAGE_DELETED: 'deleted' },
        getContext: () => context, saveChat,
        on: (name, fn) => listeners.set(name, fn), off: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); },
    };
    const store = createStateStore({ adapter, hashText: async text => `hash:${text}` });
    const queue = createChatTaskQueue();
    const manager = createRollbackManager({ adapter, store, queue, isWritable: () => writable });
    manager.bind();
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
