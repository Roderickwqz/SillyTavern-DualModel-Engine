import { expect, it, vi } from 'vitest';
import { createRollbackManager } from '../../src/rollback-manager.js';
import { createChatTaskQueue } from '../../src/task-queue.js';

it('restores the selected swipe final segment and invalidates only descendants', async () => {
    const message = { swipe_id: 1, extra: { dualModelEngine: { messageId: 'm' } } };
    const chat = [{ is_user: true }, message];
    const store = { restoreBranch: vi.fn().mockResolvedValue({ ok: true }), invalidateFrom: vi.fn().mockResolvedValue({ ok: true, snapshot: { version: 1 } }) };
    const manager = createRollbackManager({ adapter: { getContext: () => ({ chatId: 'a', chat }) }, store, queue: { enqueue: (_id, _name, fn) => fn({ throwIfAborted() {} }) }, confirm: async () => false, replayTurn: vi.fn() });
    await manager.restoreSwipe(1, 1);
    await manager.invalidateForEdit(1);
    expect(store.restoreBranch).toHaveBeenCalledWith(message, 1);
    expect(store.invalidateFrom).toHaveBeenCalledWith(1, { includeStartSelectedOnly: true, startSwipeId: 1 });
});

it('records a blank host-cloned swipe source and follows rapid 0 to 1 to 0 selection', async () => {
    const calls = {}; const first = { branchId: 'one', baseSnapshot: { version: 1 }, baseStateVersion: 1, segments: [] };
    const message = { swipe_id: 0, extra: { dualModelEngine: { messageId: 'm' } }, swipe_info: [{ extra: { dualModelEngine: { branch: first } } }, { extra: { dualModelEngine: { branch: first } } }] };
    const store = { getBranch: (_message, id) => message.swipe_info[id].extra.dualModelEngine.branch, restoreBranch: vi.fn() };
    const manager = createRollbackManager({ adapter: { events: { MESSAGE_SWIPED: 'swipe' }, on: (name, fn) => { calls[name] = fn; }, off: vi.fn(), getContext: () => ({ chatId: 'a', chat: [message] }) }, store, queue: { enqueue: (_id, _name, fn) => fn({ throwIfAborted() {} }) } });
    manager.bind(); message.swipe_id = 1; expect(calls.swipe(0)).toMatchObject({ pendingGeneration: true, sourceSwipeId: 0 });
    message.swipe_id = 0; await calls.swipe(0); expect(store.restoreBranch).toHaveBeenCalledWith(message, 0);
});

it('does not queue or persist event mutations in read-only chats', async () => {
    const enqueue = vi.fn(); const manager = createRollbackManager({ adapter: { getContext: () => ({ chatId: 'a', chat: [] }) }, store: {}, queue: { enqueue }, isWritable: () => false });
    await expect(manager.invalidateForDelete(0)).resolves.toEqual({ ok: false, reason: 'read-only' });
    expect(enqueue).not.toHaveBeenCalled();
});

it('keeps source reusable checks authoritative while appending new checks', async () => {
    const source = { branchId: 'b', baseSnapshot: { version: 1 }, baseStateVersion: 1, segments: [{ checks: [{ kind: 'check', checkId: 'source', pass: true }] }] };
    const message = { swipe_id: 0, extra: { dualModelEngine: { messageId: 'm' } }, swipe_info: [{ extra: { dualModelEngine: { branch: source } } }] };
    const manager = createRollbackManager({ adapter: { getContext: () => ({ chatId: 'a', chat: [message], chatMetadata: { dualModelEngine: { headRevision: 3 } } }) }, store: { getBranch: () => source }, queue: {} });
    expect(manager.prepareSwipeGeneration(0, 'swipe')).toMatchObject({ expectedHeadRevision: 3, reusableChecks: [{ checkId: 'source', pass: true }] });
});

it('rejects a recalculation whose guard becomes stale while waiting behind chat work', async () => {
    let release; let current = true; const queue = createChatTaskQueue(); const blocker = queue.enqueue('a', 'blocker', () => new Promise(resolve => { release = resolve; })); const replayTurn = vi.fn(); const store = { findLastValidSnapshot: vi.fn(() => ({ snapshot: { version: 0 } })), invalidateFrom: vi.fn() };
    const manager = createRollbackManager({ adapter: { getContext: () => ({ chatId: 'a', chat: [{ swipe_id: 0 }] }) }, store, queue, replayTurn }); const result = manager.recalculate(0, { isCurrent: () => current }); await vi.waitFor(() => expect(release).toBeTypeOf('function')); current = false; release(); await blocker;
    await expect(result).resolves.toEqual({ ok: false, reason: 'stale' }); expect(replayTurn).not.toHaveBeenCalled(); expect(store.invalidateFrom).not.toHaveBeenCalled();
});

it('runs recalculation normally when its queue-start guard is current', async () => {
    const replayTurn = vi.fn(async () => ({ ok: true, snapshot: { version: 1 }, stateVersion: 1 })); const manager = createRollbackManager({ adapter: { getContext: () => ({ chatId: 'a', chat: [{ swipe_id: 0 }] }) }, store: { findLastValidSnapshot: () => ({ snapshot: { version: 0 } }) }, queue: { enqueue: (_id, _name, task) => task({ throwIfAborted() {} }) }, replayTurn });
    await expect(manager.recalculate(0, { guard: () => true })).resolves.toEqual({ ok: true, lastValidVersion: 1 }); expect(replayTurn).toHaveBeenCalledOnce();
});
