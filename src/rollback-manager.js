function clone(value) { return structuredClone(value); }

export function createRollbackManager({ adapter, store, queue, confirm = async () => false, replayTurn = async () => ({ ok: false }) }) {
    const pendingSwipeSources = new Map();
    let replacement = null; let taskSequence = 0; const handlers = [];
    const context = () => adapter.getContext();
    function serialize(name, task) {
        const chatId = context().chatId;
        return queue.enqueue(chatId, `branch-${name}-${++taskSequence}`, async signal => {
            signal.throwIfAborted();
            if (context().chatId !== chatId) return { ok: false, reason: 'stale-chat' };
            return task(signal);
        });
    }
    function stableAt(index) {
        const message = context().chat[index];
        return { message, messageId: message?.extra?.dualModelEngine?.messageId };
    }
    function prepareSwipeGeneration(messageIndex, type) {
        const { message } = stableAt(messageIndex); const sourceSwipeId = pendingSwipeSources.get(messageIndex) ?? (message?.swipe_id ?? 0);
        pendingSwipeSources.delete(messageIndex); const source = store.getBranch?.(message, sourceSwipeId);
        if (!source) return { ok: false, reason: 'missing-source-branch' };
        if (type === 'regenerate') replacement = { chatId: context().chatId, deleted: false };
        const checks = source.segments?.flatMap(segment => segment.checks ?? []).filter(record => record?.kind === 'check') ?? [];
        const reusableChecks = [...new Map(checks.map(record => [record.checkId, record])).values()];
        return { ok: true, baseBranchId: source.branchId, baseSnapshot: clone(source.baseSnapshot), baseStateVersion: source.baseStateVersion, expectedHeadRevision: context().chatMetadata?.dualModelEngine?.headRevision, reusableChecks: clone(reusableChecks) };
    }
    function completeReplacement() { replacement = null; }
    async function recoverAfterDeleteNow(signal) {
        for (let index = context().chat.length - 1; index >= 0; index -= 1) {
            signal.throwIfAborted(); const message = context().chat[index];
            if (message?.is_user || message?.is_system) continue;
            const restored = await store.restoreBranch(message, message.swipe_id ?? 0);
            if (restored?.ok) return restored;
        }
        return store.restoreInitialSnapshot();
    }
    function recoverAfterDelete() { return serialize('recover-delete', recoverAfterDeleteNow); }
    async function abortReplacement() {
        const current = context().chatId; const shouldRecover = replacement?.deleted && replacement.chatId === current; const deferred = replacement?.deleted && replacement.chatId !== current;
        replacement = null; return shouldRecover ? recoverAfterDelete() : { ok: true, recovered: false, deferred: Boolean(deferred) };
    }
    function restoreSwipe(messageIndex, swipeId) {
        const { message, messageId } = stableAt(messageIndex);
        return serialize('restore', () => {
            if (!message || !context().chat.includes(message) || message.extra?.dualModelEngine?.messageId !== messageId) return { ok: false, reason: 'stale-message' };
            if ((message.swipe_id ?? 0) !== swipeId) return { ok: false, reason: 'stale-swipe' };
            return store.restoreBranch(message, swipeId);
        });
    }
    function buildRecalculationPlan(startIndex) { return context().chat.slice(startIndex).map((message, offset) => ({ message, messageIndex: startIndex + offset })).filter(({ message }) => !message?.is_user && !message?.is_system).map(({ messageIndex, message }) => ({ messageIndex, swipeId: message.swipe_id ?? 0 })); }
    async function recalculateNow(startIndex, signal) {
        let baseSnapshot = store.findLastValidSnapshot(startIndex - 1).snapshot; let lastValidVersion = baseSnapshot.version;
        for (const item of buildRecalculationPlan(startIndex)) { signal.throwIfAborted(); const value = await replayTurn({ ...item, baseSnapshot, signal }); if (!value?.ok) return { ok: false, failedAt: item.messageIndex, lastValidVersion }; baseSnapshot = value.snapshot; lastValidVersion = value.stateVersion; }
        return { ok: true, lastValidVersion };
    }
    function recalculate(startIndex) { return serialize(`recalculate-${startIndex}`, signal => recalculateNow(startIndex, signal)); }
    async function invalidateAndRecalculate(startIndex, options, signal) {
        const invalidated = await store.invalidateFrom(startIndex, options); if (!invalidated?.ok) return invalidated;
        if (!await confirm({ action: 'recalculate', startIndex, count: buildRecalculationPlan(startIndex).length, restoredVersion: invalidated.snapshot.version })) return { ok: false, reason: 'recalculation-required' };
        return recalculateNow(startIndex, signal);
    }
    function invalidateForEdit(messageIndex) { const { message, messageId } = stableAt(messageIndex); const start = message?.is_user ? messageIndex + 1 : messageIndex; return serialize('invalidate-edit', signal => { const current = context().chat[messageIndex]; if (!message || current !== message || current.extra?.dualModelEngine?.messageId !== messageId) return { ok: false, reason: 'stale-message' }; return invalidateAndRecalculate(start, { includeStartSelectedOnly: !message.is_user }, signal); }); }
    function invalidateForDelete(messageIndex) { const { message, messageId } = stableAt(messageIndex); const length = context().chat.length; return serialize('invalidate-delete', signal => { if (context().chat.length !== length || (message && (context().chat[messageIndex] !== message || message.extra?.dualModelEngine?.messageId !== messageId))) return { ok: false, reason: 'stale-delete-boundary' }; return invalidateAndRecalculate(messageIndex, { includeAllFromStart: true }, signal); }); }
    async function repairOrphanedHead() { const audit = await store.auditActiveRef(); if (audit?.ok) return { ok: true, repaired: false }; if (audit?.reason === 'assistant-text-mismatch' && audit.messageIndex >= 0) return serialize('repair-edited-head', () => store.invalidateFrom(audit.messageIndex, { includeStartSelectedOnly: true })); return recoverAfterDelete(); }
    function bind() {
        const events = adapter.events ?? {}; const on = (name, fn) => { if (name) { adapter.on(name, fn); handlers.push([name, fn]); } };
        on(events.MESSAGE_SWIPED, index => { const { message } = stableAt(index); const swipe = message?.swipe_id ?? 0; return restoreSwipe(index, swipe); });
        on(events.MESSAGE_EDITED, invalidateForEdit); on(events.MESSAGE_DELETED, index => replacement?.chatId === context().chatId ? ((replacement.deleted = true), { ok: true, ignored: 'regenerate-replacement' }) : invalidateForDelete(index));
    }
    function destroy() { while (handlers.length) { const [name, fn] = handlers.pop(); try { adapter.off(name, fn); } catch { /* best effort */ } } }
    return { prepareSwipeGeneration, completeReplacement, abortReplacement, repairOrphanedHead, restoreSwipe, recoverAfterDelete, invalidateForEdit, invalidateForDelete, buildRecalculationPlan, recalculate, bind, destroy };
}
