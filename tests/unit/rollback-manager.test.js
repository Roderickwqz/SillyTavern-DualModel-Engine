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
    expect(store.invalidateFrom).toHaveBeenCalledWith(1, { includeStartSelectedOnly: true });
});
