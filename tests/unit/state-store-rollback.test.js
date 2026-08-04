import { expect, it, vi } from 'vitest';
import { createStateStore } from '../../src/state-store.js';
import { hashText } from '../../src/identity.js';

function envelope(overrides = {}) {
    return { schemaVersion: 1, stateVersion: 3, headRevision: 7, initialSnapshot: { version: 0, nested: { seed: 1 } }, activeSnapshot: { version: 3, nested: { current: 1 } }, activeRef: { messageId: 'm2', swipeId: 0, branchId: 'b2' }, taskStatus: { state: 'idle', requestId: null }, ...overrides };
}

function branch(id, version, status = 'committed', text = '') {
    return { branchId: id, baseStateVersion: Math.max(0, version - 1), baseSnapshot: { version: Math.max(0, version - 1) }, status, segments: [{ postSnapshot: { version, nested: { branch: id } }, assistantTextHash: text ? `hash:${text}` : undefined }] };
}

function assistant(id, branches, selected = 0, mes = '') {
    const swipes = branches.map(item => ({ extra: { dualModelEngine: { messageId: id, branch: item } } }));
    return { mes, extra: { dualModelEngine: structuredClone(swipes[selected].extra.dualModelEngine) }, swipe_id: selected, swipe_info: swipes };
}

function setup({ chat, metadata = envelope(), saveChat = vi.fn().mockResolvedValue(undefined), hash = async text => `hash:${text}` } = {}) {
    const context = { chatId: 'chat-a', chatMetadata: { dualModelEngine: metadata }, chat: chat ?? [] };
    return { context, saveChat, store: createStateStore({ adapter: { getContext: () => context, saveChat }, hashText: hash }) };
}

it('removes a non-selected branch transactionally without changing the selected mirror', async () => {
    const selected = branch('selected', 3); const removed = branch('removed', 2);
    const message = assistant('m1', [selected, removed], 0);
    const { store, context, saveChat } = setup({ chat: [message] });
    await expect(store.removeBranch(message, 1)).resolves.toEqual({ ok: true });
    expect(message.swipe_info[1].extra.dualModelEngine.branch).toBeUndefined();
    expect(message.extra.dualModelEngine.branch).toEqual(selected);
    expect(context.chatMetadata.dualModelEngine.headRevision).toBe(8);
    expect(saveChat).toHaveBeenCalledOnce();
});

it('removes selected branch from both selected namespaces and restores all affected state on save failure', async () => {
    const message = assistant('m1', [branch('selected', 3), branch('other', 2)], 0);
    const { store, context, saveChat } = setup({ chat: [message], saveChat: vi.fn().mockRejectedValue(new Error('disk')) });
    const before = structuredClone(context);
    await expect(store.removeBranch(message, 0)).resolves.toMatchObject({ ok: false, reason: 'save-failed' });
    expect(context).toEqual(before); expect(saveChat).toHaveBeenCalledOnce();
});

it('rejects missing identities and invalid indices for removal without saving', async () => {
    const message = assistant('m1', [branch('selected', 3)]);
    const { store, context, saveChat } = setup({ chat: [message] });
    delete message.swipe_info[0].extra.dualModelEngine.messageId;
    const before = structuredClone(context);
    await expect(store.removeBranch(message, 0)).resolves.toEqual({ ok: false, reason: 'invalid-identity' });
    await expect(store.removeBranch(message, 9)).resolves.toEqual({ ok: false, reason: 'stale-swipe' });
    expect(context).toEqual(before); expect(saveChat).not.toHaveBeenCalled();
});

it('restores the persisted initial snapshot as a clone and clears active state in one save', async () => {
    const { store, context, saveChat } = setup();
    await expect(store.restoreInitialSnapshot()).resolves.toEqual({ ok: true, snapshot: { version: 0, nested: { seed: 1 } } });
    const saved = context.chatMetadata.dualModelEngine;
    expect(saved).toMatchObject({ stateVersion: 0, headRevision: 8, activeRef: null, activeSnapshot: { version: 0, nested: { seed: 1 } } });
    saved.activeSnapshot.nested.seed = 99;
    expect(saved.initialSnapshot.nested.seed).toBe(1); expect(saveChat).toHaveBeenCalledOnce();
});

it('rolls back initial restoration and rejects malformed persisted initial/version inputs without saves', async () => {
    const rejected = setup({ saveChat: vi.fn().mockRejectedValue(new Error('disk')) });
    const before = structuredClone(rejected.context);
    await expect(rejected.store.restoreInitialSnapshot()).resolves.toMatchObject({ ok: false, reason: 'save-failed' });
    expect(rejected.context).toEqual(before);
    const malformed = setup({ metadata: envelope({ initialSnapshot: { version: Number.NaN } }) }); const malformedBefore = structuredClone(malformed.context);
    await expect(malformed.store.restoreInitialSnapshot()).resolves.toEqual({ ok: false, reason: 'invalid-envelope' });
    expect(malformed.context).toEqual(malformedBefore); expect(malformed.saveChat).not.toHaveBeenCalled();
});

it('finds the closest visible selected committed terminal snapshot and returns isolated clones', () => {
    const old = assistant('m0', [branch('old', 1)]);
    const selected = branch('selected', 2); const hidden = branch('hidden', 8);
    const nearest = assistant('m2', [selected, hidden], 0);
    const terminalMismatch = assistant('m3', [{ ...branch('bad', 3), segments: [{ postSnapshot: { version: Number.NaN } }] }]);
    const { store, context } = setup({ chat: [old, { is_user: true }, nearest, { is_system: true }, terminalMismatch] });
    const found = store.findLastValidSnapshot(4);
    expect(found).toEqual({ snapshot: { version: 2, nested: { branch: 'selected' } }, activeRef: { messageId: 'm2', swipeId: 0, branchId: 'selected' } });
    found.snapshot.nested.branch = 'mutated'; found.activeRef.branchId = 'mutated';
    expect(context.chat[2].swipe_info[0].extra.dualModelEngine.branch.segments[0].postSnapshot.nested.branch).toBe('selected');
    expect(store.findLastValidSnapshot(-1)).toEqual({ snapshot: { version: 0, nested: { seed: 1 } }, activeRef: null });
});

it('invalidates only the selected edited assistant branch and every descendant branch', async () => {
    const start0 = branch('start0', 3); const start1 = branch('start1', 3);
    const descendant0 = branch('next0', 4); const descendant1 = branch('next1', 4);
    const prior = assistant('m0', [branch('prior', 2)]); const start = assistant('m1', [start0, start1], 0); const next = assistant('m2', [descendant0, descendant1], 1);
    const { store, context, saveChat } = setup({ chat: [prior, start, next] });
    await expect(store.invalidateFrom(1, { includeStartSelectedOnly: true })).resolves.toMatchObject({ ok: true, snapshot: { version: 2 }, activeRef: { messageId: 'm0', swipeId: 0, branchId: 'prior' } });
    expect([start0.status, start1.status, descendant0.status, descendant1.status]).toEqual(['stale', 'committed', 'stale', 'stale']);
    expect(context.chatMetadata.dualModelEngine).toMatchObject({ stateVersion: 2, activeRef: { messageId: 'm0', swipeId: 0, branchId: 'prior' }, headRevision: 8 });
    expect(saveChat).toHaveBeenCalledOnce();
});

it('invalidates every assistant branch from a deleted user boundary and rolls all state back on save failure', async () => {
    const first = assistant('m1', [branch('a', 2), branch('b', 2)], 1); const second = assistant('m2', [branch('c', 3)]);
    const rejected = setup({ chat: [{ is_user: true }, first, second], saveChat: vi.fn().mockRejectedValue(new Error('disk')) }); const before = structuredClone(rejected.context);
    await expect(rejected.store.invalidateFrom(0, { includeAllFromStart: true })).resolves.toMatchObject({ ok: false, reason: 'save-failed' });
    expect(rejected.context).toEqual(before);
    const normal = setup({ chat: [{ is_user: true }, first, second] });
    await normal.store.invalidateFrom(0, { includeAllFromStart: true });
    expect(first.swipe_info.map(s => s.extra.dualModelEngine.branch.status)).toEqual(['stale', 'stale']);
    expect(second.swipe_info[0].extra.dualModelEngine.branch.status).toBe('stale');
});

it('rejects malformed invalidation inputs without mutation or saves', async () => {
    const { store, context, saveChat } = setup({ chat: [assistant('m1', [branch('a', 2)])] }); const before = structuredClone(context);
    await expect(store.invalidateFrom(0.5)).resolves.toEqual({ ok: false, reason: 'invalid-context' });
    await expect(store.invalidateFrom(-1)).resolves.toEqual({ ok: false, reason: 'invalid-context' });
    context.chatMetadata.dualModelEngine.headRevision = Number.NaN;
    await expect(store.invalidateFrom(0)).resolves.toEqual({ ok: false, reason: 'invalid-context' });
    expect(saveChat).not.toHaveBeenCalled();
    expect(context.chat[0]).toEqual(before.chat[0]);
});

it('rejects invalid invalidation options without changing persisted state', async () => {
    const { store, context, saveChat } = setup({ chat: [assistant('m1', [branch('a', 2)])] }); const before = structuredClone(context);
    await expect(store.invalidateFrom(0, { includeAllFromStart: 'yes' })).resolves.toEqual({ ok: false, reason: 'invalid-options' });
    expect(context).toEqual(before); expect(saveChat).not.toHaveBeenCalled();
});

it('audits initial, orphaned, missing, wrong swipe, version, text and valid active references without mutation', async () => {
    const good = assistant('m1', [branch('b0', 2, 'committed', 'actual'), branch('b1', 3, 'committed', 'other')], 0, 'actual');
    good.swipe_info[0].extra.dualModelEngine.branch.segments[0].assistantTextHash = await hashText('actual');
    const { store, context, saveChat } = setup({ chat: [good], metadata: envelope({ stateVersion: 2, activeSnapshot: { version: 2 }, activeRef: { messageId: 'm1', swipeId: 0, branchId: 'b0' } }), hash: hashText });
    const before = structuredClone(context);
    await expect(store.auditActiveRef()).resolves.toEqual({ ok: true });
    context.chatMetadata.dualModelEngine.activeRef = null;
    await expect(store.auditActiveRef()).resolves.toEqual({ ok: false, reason: 'orphaned-active-ref' });
    context.chatMetadata.dualModelEngine.activeRef = { messageId: 'missing', swipeId: 0, branchId: 'b0' };
    await expect(store.auditActiveRef()).resolves.toEqual({ ok: false, reason: 'orphaned-active-ref', messageIndex: -1 });
    context.chatMetadata.dualModelEngine.activeRef = { messageId: 'm1', swipeId: 1, branchId: 'b1' };
    await expect(store.auditActiveRef()).resolves.toEqual({ ok: false, reason: 'orphaned-active-ref', messageIndex: 0 });
    context.chatMetadata.dualModelEngine.activeRef = { messageId: 'm1', swipeId: 0, branchId: 'b0' }; context.chatMetadata.dualModelEngine.stateVersion = 99;
    await expect(store.auditActiveRef()).resolves.toEqual({ ok: false, reason: 'orphaned-active-ref', messageIndex: 0 });
    context.chatMetadata.dualModelEngine.stateVersion = 2; good.mes = 'edited';
    await expect(store.auditActiveRef()).resolves.toEqual({ ok: false, reason: 'assistant-text-mismatch', messageIndex: 0 });
    expect(saveChat).not.toHaveBeenCalled(); expect(context.chat[0].swipe_info[0].extra.dualModelEngine.branch.status).toBe(before.chat[0].swipe_info[0].extra.dualModelEngine.branch.status);
});

it('audits null initial heads and every invalid committed-reference dimension', async () => {
    const selected = branch('b0', 2, 'committed', 'ok'); const nonselected = branch('b1', 2, 'committed', 'also');
    const message = assistant('m1', [selected, nonselected], 0, 'ok');
    const { store, context, saveChat } = setup({ chat: [message], metadata: envelope({ stateVersion: 0, activeSnapshot: { version: 0, nested: { seed: 1 } }, activeRef: null }) });
    await expect(store.auditActiveRef()).resolves.toEqual({ ok: true });
    const cases = [
        [{ messageId: 'm1', swipeId: 0, branchId: 'wrong' }, 'orphaned-active-ref'],
        [{ messageId: 'm1', swipeId: 0, branchId: 'b0' }, 'orphaned-active-ref'],
    ];
    selected.status = 'stale'; context.chatMetadata.dualModelEngine.stateVersion = 2;
    for (const [activeRef, reason] of cases) {
        context.chatMetadata.dualModelEngine.activeRef = activeRef;
        await expect(store.auditActiveRef()).resolves.toEqual({ ok: false, reason, messageIndex: 0 });
    }
    expect(saveChat).not.toHaveBeenCalled();
});
