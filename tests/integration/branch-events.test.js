import { expect, it, vi } from 'vitest';
import { createRollbackManager } from '../../src/rollback-manager.js';

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
