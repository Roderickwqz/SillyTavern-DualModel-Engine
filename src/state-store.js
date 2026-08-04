import { NAMESPACE } from './constants.js';
import { ensureMessageId } from './identity.js';

function result(reason, error) {
    return error === undefined ? { ok: false, reason } : { ok: false, reason, error };
}

function clone(value) {
    return structuredClone(value);
}

function getNamespace(message, swipeId) {
    return message?.swipe_info?.[swipeId]?.extra?.[NAMESPACE] ?? null;
}

export function createStateStore({ adapter, makeId = () => crypto.randomUUID(), hashText: textHash }) {
    function loadEnvelope() {
        const envelope = adapter.getContext?.()?.chatMetadata?.[NAMESPACE];
        return envelope && typeof envelope === 'object' ? { ok: true, value: envelope } : result('missing-envelope');
    }

    function getBranch(message, swipeId) {
        return getNamespace(message, swipeId)?.branch ?? null;
    }

    function ensureBranch(message, swipeId, baseSnapshot, baseStateVersion, branchId, replaceExisting = false) {
        ensureMessageId(message, makeId);
        message.swipe_info ??= [];
        message.swipe_info[swipeId] ??= { extra: {} };
        const swipe = message.swipe_info[swipeId];
        swipe.extra ??= {};
        swipe.extra[NAMESPACE] ??= {};
        const current = swipe.extra[NAMESPACE];
        current.messageId = message.extra[NAMESPACE].messageId;
        if (!current.branch || (replaceExisting && current.branch.branchId !== branchId)) {
            current.branch = {
                branchId: branchId ?? makeId(),
                baseStateVersion,
                baseSnapshot: clone(baseSnapshot),
                segments: [],
                status: 'pending',
            };
        }
        message.extra[NAMESPACE] = clone(current);
        return current.branch;
    }

    function validCommitContext(input, context, envelope) {
        if (!context || context.chatId !== input.chatId) return 'stale-chat';
        if (!envelope) return 'missing-envelope';
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

        let textHashValue;
        try {
            textHashValue = await textHash(input.assistantText);
        } catch (error) {
            return result('hash-failed', error);
        }

        const metadataBefore = clone(envelope);
        const messageExtraBefore = clone(input.message.extra);
        const swipeBefore = clone(input.message.swipe_info[input.swipeId]);
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
            envelope.stateVersion = input.nextState.version;
            envelope.headRevision += 1;
            envelope.activeSnapshot = clone(input.nextState);
            envelope.activeRef = { messageId: input.messageId, swipeId: input.swipeId, branchId: input.branchId };
            envelope.lastCommittedRequestId = input.requestId;
            envelope.taskStatus = { state: 'idle', requestId: null };
            await adapter.saveChat();
            return { ok: true, stateVersion: envelope.stateVersion, headRevision: envelope.headRevision, branch: clone(branch) };
        } catch (error) {
            context.chatMetadata[NAMESPACE] = metadataBefore;
            input.message.extra = messageExtraBefore;
            input.message.swipe_info[input.swipeId] = swipeBefore;
            return result('save-failed', error);
        }
    }

    async function restoreBranch(message, swipeId) {
        const context = adapter.getContext?.();
        const envelope = context?.chatMetadata?.[NAMESPACE];
        if (!envelope) return result('missing-envelope');
        if (!Array.isArray(context.chat) || !context.chat.includes(message)) return result('stale-message');
        if ((message.swipe_id ?? 0) !== swipeId || !message.swipe_info?.[swipeId]) return result('stale-swipe');
        const namespace = getNamespace(message, swipeId);
        const branch = namespace?.branch;
        const snapshot = branch?.segments?.at(-1)?.postSnapshot;
        if (!snapshot) return result('missing-snapshot');

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
        if (!Array.isArray(context.chat) || !Number.isInteger(messageIndex)) return result('invalid-context');
        const metadataBefore = clone(envelope);
        const branchBefore = [];
        try {
            for (const message of context.chat.slice(messageIndex + 1)) {
                for (let swipeId = 0; swipeId < (message.swipe_info?.length ?? 0); swipeId += 1) {
                    const swipe = message.swipe_info[swipeId];
                    if (getNamespace(message, swipeId)?.branch) {
                        branchBefore.push([message, swipeId, clone(swipe), clone(message.extra)]);
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
            for (const [message, swipeId, swipe, extra] of branchBefore) {
                message.swipe_info[swipeId] = swipe;
                message.extra = extra;
            }
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

    return { loadEnvelope, getBranch, ensureBranch, commitSegment, restoreBranch, markStaleAfter, listRuleRecords };
}
