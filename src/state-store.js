import { NAMESPACE } from './constants.js';
import { ensureMessageId } from './identity.js';
import { createEmptyEnvelope } from './migrations.js';

function result(reason, error) {
    return error === undefined ? { ok: false, reason } : { ok: false, reason, error };
}

function clone(value) {
    return structuredClone(value);
}

function isPlainObject(value) {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
}

function isRevision(value) {
    return Number.isSafeInteger(value) && value >= 0;
}

function validEnvelope(envelope) {
    return isPlainObject(envelope) && isRevision(envelope.stateVersion) && isRevision(envelope.headRevision);
}

function getNamespace(message, swipeId) {
    return message?.swipe_info?.[swipeId]?.extra?.[NAMESPACE] ?? null;
}

export function createStateStore({ adapter, makeId = () => crypto.randomUUID(), hashText: textHash }) {
    function loadEnvelope() {
        const envelope = adapter.getContext?.()?.chatMetadata?.[NAMESPACE];
        if (!envelope) return result('missing-envelope');
        return validEnvelope(envelope) ? { ok: true, value: envelope } : result('invalid-envelope');
    }

    function getBranch(message, swipeId) {
        return getNamespace(message, swipeId)?.branch ?? null;
    }

    async function ensureEnvelope({ presetId, initialState }) {
        const context = adapter.getContext?.();
        if (!context?.chatMetadata) return result('invalid-context');
        const existing = context.chatMetadata[NAMESPACE];
        if (existing) return validEnvelope(existing) ? { ok: true, value: existing, created: false } : result('invalid-envelope');
        const before = clone(context.chatMetadata);
        try {
            const envelope = createEmptyEnvelope({ presetId, initialState });
            context.chatMetadata[NAMESPACE] = envelope;
            await adapter.saveChat();
            return { ok: true, value: envelope, created: true };
        } catch (error) { context.chatMetadata = before; return result('save-failed', error); }
    }

    function prepareSwipeGeneration({ target }) {
        const swipeId = target?.swipe_id ?? 0; const branch = getBranch(target, swipeId);
        if (!branch?.baseSnapshot || !Number.isSafeInteger(branch.baseStateVersion)) return result('missing-source-branch');
        return { ok: true, baseSnapshot: clone(branch.baseSnapshot), baseStateVersion: branch.baseStateVersion, baseBranchId: branch.branchId, targetMessageId: target.extra?.[NAMESPACE]?.messageId };
    }

    function ensureBranch(message, swipeId, baseSnapshot, baseStateVersion, branchId, replaceExisting = false) {
        if (!isPlainObject(message) || !Array.isArray(message.swipe_info)
            || !Number.isSafeInteger(swipeId) || swipeId < 0 || swipeId >= message.swipe_info.length
            || !isPlainObject(message.swipe_info[swipeId])) throw new Error('Invalid swipe index');
        const baseSnapshotCopy = clone(baseSnapshot);
        ensureMessageId(message, makeId);
        const swipe = message.swipe_info[swipeId];
        swipe.extra ??= {};
        swipe.extra[NAMESPACE] ??= {};
        const current = swipe.extra[NAMESPACE];
        current.messageId = message.extra[NAMESPACE].messageId;
        if (!current.branch || (replaceExisting && current.branch.branchId !== branchId)) {
            current.branch = {
                branchId: branchId ?? makeId(),
                baseStateVersion,
                baseSnapshot: baseSnapshotCopy,
                segments: [],
                status: 'pending',
            };
        }
        message.extra[NAMESPACE] = clone(current);
        return current.branch;
    }

    function validCommitContext(input, context, envelope, capturedChat = null) {
        if (!context || context.chatId !== input.chatId) return 'stale-chat';
        if (capturedChat !== null && context.chat !== capturedChat) return 'stale-chat';
        if (!envelope) return 'missing-envelope';
        if (!validEnvelope(envelope)) return 'invalid-envelope';
        if (!input.branchId || !input.requestId || !input.messageId) return 'missing-identity';
        if (!Array.isArray(context.chat) || !context.chat.includes(input.message)
            || input.message?.extra?.[NAMESPACE]?.messageId !== input.messageId) return 'stale-message';
        if ((input.message.swipe_id ?? 0) !== input.swipeId) return 'stale-swipe';
        if (!input.message.swipe_info?.[input.swipeId]) return 'stale-swipe';
        const existingBranch = getBranch(input.message, input.swipeId);
        if (input.isContinue && existingBranch?.branchId !== input.branchId) return 'branch-conflict';
        if (envelope.headRevision !== input.expectedHeadRevision) return 'head-conflict';
        if (envelope.stateVersion !== input.baseStateVersion) return 'state-conflict';
        if (input.nextState?.version !== input.baseStateVersion + 1) return 'invalid-next-version';
        if (envelope.lastCommittedRequestId === input.requestId) return 'duplicate-request';
        return null;
    }

    async function commitSegment(input) {
        const context = adapter.getContext?.();
        const envelope = context?.chatMetadata?.[NAMESPACE];
        const invalid = validCommitContext(input, context, envelope);
        if (invalid) return result(invalid);
        const capturedChat = context.chat;

        let textHashValue;
        try {
            textHashValue = await textHash(input.assistantText);
        } catch (error) {
            return result('hash-failed', error);
        }

        const contextAfterHash = adapter.getContext?.();
        const envelopeAfterHash = contextAfterHash?.chatMetadata?.[NAMESPACE];
        const staleAfterHash = validCommitContext(input, contextAfterHash, envelopeAfterHash, capturedChat);
        if (staleAfterHash) return result(staleAfterHash);

        const metadataBefore = clone(envelopeAfterHash);
        const messageExtraBefore = clone(input.message.extra);
        const swipesBefore = clone(input.message.swipe_info);
        try {
            const branch = ensureBranch(input.message, input.swipeId, input.baseSnapshot, input.baseStateVersion, input.branchId, !input.isContinue);
            const segment = {
                requestId: input.requestId,
                userMessageId: input.userMessageId,
                assistantTextHash: textHashValue,
                checks: clone(input.checks),
                patch: clone(input.patch),
                postSnapshot: clone(input.nextState),
            };
            if (input.isContinue) branch.segments.push(segment);
            else branch.segments = [segment];
            branch.status = 'committed';
            envelopeAfterHash.stateVersion = input.nextState.version;
            envelopeAfterHash.headRevision += 1;
            envelopeAfterHash.activeSnapshot = clone(input.nextState);
            envelopeAfterHash.activeRef = { messageId: input.messageId, swipeId: input.swipeId, branchId: input.branchId };
            envelopeAfterHash.lastCommittedRequestId = input.requestId;
            envelopeAfterHash.taskStatus = { state: 'idle', requestId: null };
            await adapter.saveChat();
            return { ok: true, stateVersion: envelopeAfterHash.stateVersion, headRevision: envelopeAfterHash.headRevision, branch: clone(branch) };
        } catch (error) {
            contextAfterHash.chatMetadata[NAMESPACE] = metadataBefore;
            input.message.extra = messageExtraBefore;
            input.message.swipe_info = swipesBefore;
            return result('save-failed', error);
        }
    }

    async function restoreBranch(message, swipeId) {
        const context = adapter.getContext?.();
        const envelope = context?.chatMetadata?.[NAMESPACE];
        if (!envelope) return result('missing-envelope');
        if (!validEnvelope(envelope)) return result('invalid-envelope');
        if (!Array.isArray(context.chat) || !context.chat.includes(message)) return result('stale-message');
        if ((message.swipe_id ?? 0) !== swipeId || !message.swipe_info?.[swipeId]) return result('stale-swipe');
        const namespace = getNamespace(message, swipeId);
        const branch = namespace?.branch;
        const snapshot = branch?.segments?.at(-1)?.postSnapshot;
        if (!snapshot) return result('missing-snapshot');
        if (typeof namespace.messageId !== 'string' || !namespace.messageId
            || typeof branch.branchId !== 'string' || !branch.branchId) return result('invalid-identity');
        if (!isPlainObject(snapshot) || !isRevision(snapshot.version)) return result('invalid-snapshot');

        const metadataBefore = clone(envelope);
        const messageExtraBefore = clone(message.extra);
        const swipeBefore = clone(message.swipe_info[swipeId]);
        try {
            message.extra ??= {};
            message.extra[NAMESPACE] = clone(namespace);
            envelope.activeSnapshot = clone(snapshot);
            envelope.stateVersion = snapshot.version;
            envelope.activeRef = { messageId: namespace.messageId, swipeId, branchId: branch.branchId };
            envelope.headRevision += 1;
            await adapter.saveChat();
            return { ok: true, snapshot: clone(snapshot) };
        } catch (error) {
            context.chatMetadata[NAMESPACE] = metadataBefore;
            message.extra = messageExtraBefore;
            message.swipe_info[swipeId] = swipeBefore;
            return result('save-failed', error);
        }
    }

    async function markStaleAfter(messageIndex) {
        const context = adapter.getContext?.();
        const envelope = context?.chatMetadata?.[NAMESPACE];
        if (!envelope) return result('missing-envelope');
        if (!validEnvelope(envelope)) return result('invalid-envelope');
        if (!Array.isArray(context.chat) || !Number.isInteger(messageIndex)) return result('invalid-context');
        const metadataBefore = clone(envelope);
        const messagesBefore = new Map();
        try {
            for (const message of context.chat.slice(messageIndex + 1)) {
                for (let swipeId = 0; swipeId < (message.swipe_info?.length ?? 0); swipeId += 1) {
                    const swipe = message.swipe_info[swipeId];
                    if (getNamespace(message, swipeId)?.branch) {
                        if (!messagesBefore.has(message)) {
                            messagesBefore.set(message, { extra: clone(message.extra), swipes: clone(message.swipe_info) });
                        }
                        swipe.extra[NAMESPACE].branch.status = 'stale';
                        if ((message.swipe_id ?? 0) === swipeId) message.extra[NAMESPACE] = clone(swipe.extra[NAMESPACE]);
                    }
                }
            }
            envelope.headRevision += 1;
            await adapter.saveChat();
            return { ok: true };
        } catch (error) {
            context.chatMetadata[NAMESPACE] = metadataBefore;
            for (const [message, before] of messagesBefore) {
                message.extra = before.extra;
                message.swipe_info = before.swipes;
            }
            return result('save-failed', error);
        }
    }

    function findLastValidSnapshot(messageIndex) {
        const context = adapter.getContext?.(); const envelope = context?.chatMetadata?.[NAMESPACE];
        if (!validEnvelope(envelope)) return null;
        for (let index = Math.min(messageIndex, (context.chat?.length ?? 0) - 1); index >= 0; index -= 1) {
            const message = context.chat[index]; const swipeId = message?.swipe_id ?? 0;
            const branch = getBranch(message, swipeId); const snapshot = branch?.status === 'committed' ? branch.segments?.at(-1)?.postSnapshot : null;
            const messageId = message?.extra?.[NAMESPACE]?.messageId;
            if (isPlainObject(snapshot) && isRevision(snapshot.version) && messageId && branch.branchId) return { snapshot: clone(snapshot), activeRef: { messageId, swipeId, branchId: branch.branchId } };
        }
        return { snapshot: clone(envelope.initialSnapshot), activeRef: null };
    }

    async function invalidateFrom(startIndex, options = {}) {
        const context = adapter.getContext?.(); const envelope = context?.chatMetadata?.[NAMESPACE];
        if (!validEnvelope(envelope) || !Array.isArray(context?.chat) || !Number.isInteger(startIndex)) return result('invalid-context');
        const metadataBefore = clone(envelope); const messagesBefore = new Map();
        const boundary = findLastValidSnapshot(startIndex - 1);
        if (!boundary) return result('invalid-envelope');
        try {
            for (let index = Math.max(0, startIndex); index < context.chat.length; index += 1) {
                const message = context.chat[index]; if (message?.is_user || message?.is_system) continue;
                const all = options.includeAllFromStart || index > startIndex;
                const selected = message.swipe_id ?? 0;
                for (let swipeId = 0; swipeId < (message.swipe_info?.length ?? 0); swipeId += 1) {
                    const branch = getBranch(message, swipeId);
                    if (!branch || (!all && options.includeStartSelectedOnly && swipeId !== selected)) continue;
                    if (!messagesBefore.has(message)) messagesBefore.set(message, { extra: clone(message.extra), swipes: clone(message.swipe_info) });
                    branch.status = 'stale';
                    if (swipeId === selected) message.extra[NAMESPACE] = clone(message.swipe_info[swipeId].extra[NAMESPACE]);
                }
            }
            envelope.activeSnapshot = clone(boundary.snapshot); envelope.stateVersion = boundary.snapshot.version; envelope.activeRef = clone(boundary.activeRef);
            envelope.headRevision += 1; await adapter.saveChat();
            return { ok: true, snapshot: clone(boundary.snapshot), activeRef: clone(boundary.activeRef) };
        } catch (error) {
            context.chatMetadata[NAMESPACE] = metadataBefore;
            for (const [message, before] of messagesBefore) { message.extra = before.extra; message.swipe_info = before.swipes; }
            return result('save-failed', error);
        }
    }

    async function removeBranch(message, swipeId) {
        const context = adapter.getContext?.(); const envelope = context?.chatMetadata?.[NAMESPACE];
        if (!validEnvelope(envelope) || !context?.chat?.includes(message)) return result('stale-message');
        if (!Number.isInteger(swipeId) || swipeId < 0 || !message.swipe_info?.[swipeId]) return result('stale-swipe');
        const metadataBefore = clone(envelope); const extraBefore = clone(message.extra); const swipesBefore = clone(message.swipe_info);
        try {
            delete message.swipe_info[swipeId].extra?.[NAMESPACE]?.branch;
            if ((message.swipe_id ?? 0) === swipeId) delete message.extra?.[NAMESPACE]?.branch;
            envelope.headRevision += 1; await adapter.saveChat(); return { ok: true };
        } catch (error) { context.chatMetadata[NAMESPACE] = metadataBefore; message.extra = extraBefore; message.swipe_info = swipesBefore; return result('save-failed', error); }
    }

    async function restoreInitialSnapshot() {
        const context = adapter.getContext?.(); const envelope = context?.chatMetadata?.[NAMESPACE];
        if (!validEnvelope(envelope) || !isPlainObject(envelope.initialSnapshot) || !isRevision(envelope.initialSnapshot.version)) return result('invalid-envelope');
        const before = clone(envelope);
        try { envelope.activeSnapshot = clone(envelope.initialSnapshot); envelope.stateVersion = envelope.initialSnapshot.version; envelope.activeRef = null; envelope.headRevision += 1; await adapter.saveChat(); return { ok: true, snapshot: clone(envelope.activeSnapshot) }; }
        catch (error) { context.chatMetadata[NAMESPACE] = before; return result('save-failed', error); }
    }

    async function auditActiveRef() {
        const context = adapter.getContext?.(); const envelope = context?.chatMetadata?.[NAMESPACE];
        if (!validEnvelope(envelope)) return result('invalid-envelope');
        if (!envelope.activeRef) return JSON.stringify(envelope.activeSnapshot) === JSON.stringify(envelope.initialSnapshot) ? { ok: true } : result('orphaned-active-ref');
        const { messageId, swipeId, branchId } = envelope.activeRef;
        const messageIndex = context?.chat?.findIndex(message => message?.extra?.[NAMESPACE]?.messageId === messageId) ?? -1;
        const message = context?.chat?.[messageIndex]; const branch = getBranch(message, swipeId); const segment = branch?.segments?.at(-1);
        if (!message || (message.swipe_id ?? 0) !== swipeId || branch?.branchId !== branchId || branch.status !== 'committed' || segment?.postSnapshot?.version !== envelope.stateVersion) return { ok: false, reason: 'orphaned-active-ref', messageIndex };
        try { if (segment.assistantTextHash !== await textHash(message.mes ?? '')) return { ok: false, reason: 'assistant-text-mismatch', messageIndex }; }
        catch (error) { return result('hash-failed', error); }
        return { ok: true };
    }

    async function markBranchFailed({ chatId, messageId, swipeId, branchId, requestId, baseSnapshot, baseStateVersion, isContinue, baseBranchId }) {
        const context = adapter.getContext?.();
        const envelope = context?.chatMetadata?.[NAMESPACE];
        if (!context || context.chatId !== chatId) return result('stale-chat');
        if (!validEnvelope(envelope)) return result('invalid-envelope');
        const message = context.chat?.find(item => item?.extra?.[NAMESPACE]?.messageId === messageId);
        if (!message) return result('stale-message');
        if ((message.swipe_id ?? 0) !== swipeId || !message.swipe_info?.[swipeId]) return result('stale-swipe');
        let branch = getBranch(message, swipeId);
        if (isContinue && branch?.branchId !== branchId) return result('branch-conflict');
        const metadataBefore = clone(envelope); const extraBefore = clone(message.extra); const swipesBefore = clone(message.swipe_info);
        if (!isContinue && branch?.branchId !== branchId && baseSnapshot && Number.isSafeInteger(baseStateVersion)) {
            if (branch && (!baseBranchId || branch.branchId !== baseBranchId)) return result('branch-conflict');
            branch = ensureBranch(message, swipeId, baseSnapshot, baseStateVersion, branchId, true);
        }
        if (branch?.branchId !== branchId) return result('branch-conflict');
        try {
            branch.status = 'stale';
            message.extra[NAMESPACE] = clone(message.swipe_info[swipeId].extra[NAMESPACE]);
            envelope.taskStatus = { state: 'failed', requestId };
            envelope.headRevision += 1;
            await adapter.saveChat();
            return { ok: true };
        } catch (error) {
            context.chatMetadata[NAMESPACE] = metadataBefore; message.extra = extraBefore; message.swipe_info = swipesBefore;
            return result('save-failed', error);
        }
    }

    function listRuleRecords() {
        const records = new Map();
        for (const message of adapter.getContext?.()?.chat ?? []) {
            for (const swipe of message.swipe_info ?? []) {
                for (const segment of swipe.extra?.[NAMESPACE]?.branch?.segments ?? []) {
                    for (const record of segment.checks ?? []) {
                        if (record?.checkId) records.set(record.checkId, clone(record));
                    }
                }
            }
        }
        return [...records.values()].map(clone);
    }

    return { loadEnvelope, ensureEnvelope, prepareSwipeGeneration, getBranch, ensureBranch, commitSegment, restoreBranch, markStaleAfter, markBranchFailed, listRuleRecords, findLastValidSnapshot, invalidateFrom, removeBranch, restoreInitialSnapshot, auditActiveRef };
}
