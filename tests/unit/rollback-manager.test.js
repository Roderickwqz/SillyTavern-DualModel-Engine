import { expect, it, vi } from 'vitest';
import { createRollbackManager } from '../../src/rollback-manager.js';

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
