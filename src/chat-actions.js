import { createRuleEngine } from './rule-engine.js';

function safeText(value) { return typeof value === 'string' ? value : JSON.stringify(value); }
function current(deps) {
    const context = deps.adapter.getContext?.(); const loaded = deps.store.loadEnvelope?.(); const envelope = loaded?.value ?? loaded;
    if (!context?.chatId || context.groupId || !deps.config?.().enabled || deps.orchestrator?.getActiveGeneration?.() || !envelope?.activeRef) return null;
    return { context, envelope, ref: structuredClone(envelope.activeRef), chat: context.chat, metadata: context.chatMetadata, headRevision: envelope.headRevision, stateVersion: envelope.stateVersion, preset: structuredClone(envelope.preset) };
}
function same(deps, captured) {
    const context = deps.adapter.getContext?.(); const loaded = deps.store.loadEnvelope?.(); const envelope = loaded?.value ?? loaded;
    return Boolean(context?.chatId === captured.context.chatId && context.chat === captured.chat && context.chatMetadata === captured.metadata && envelope === captured.envelope && envelope.headRevision === captured.headRevision && envelope.stateVersion === captured.stateVersion && JSON.stringify(envelope.activeRef) === JSON.stringify(captured.ref) && JSON.stringify(envelope.preset) === JSON.stringify(captured.preset));
}
function download(name, value) {
    const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2); const url = globalThis.URL.createObjectURL(new globalThis.Blob([text], { type: 'application/json' })); const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); globalThis.queueMicrotask(() => globalThis.URL.revokeObjectURL(url));
}

export function createChatActions(deps) {
    async function transaction(label, work) {
        const captured = current(deps); if (!captured) return { ok: false, reason: 'not-writable' };
        return deps.queue.enqueue(captured.context.chatId, `${label}-${deps.makeId()}`, async signal => { signal.throwIfAborted(); if (!same(deps, captured)) return { ok: false, reason: 'stale' }; return work(captured, signal); });
    }
    return {
        recalculate: async () => { const captured = current(deps); if (!captured) return { ok: false, reason: 'not-writable' }; const plan = deps.rollbackManager.buildRecalculationPlan?.(deps.currentInvalidIndex?.()); if (plan && !await deps.confirm({ action: 'recalculate', plan })) return { ok: false, reason: 'cancelled' }; return deps.rollbackManager.recalculate(deps.currentInvalidIndex?.()); },
        reroll: () => transaction('reroll', async captured => {
            const records = deps.ledger.list(); const old = deps.selectedCheck?.() ?? records.findLast(record => record.kind === 'check' && record.branchId === captured.ref.branchId); if (!old?.request) return { ok: false, reason: 'missing-check' };
            const preset = deps.preset(captured.envelope.preset.id); if (!preset?.readActor) return { ok: false, reason: 'rules-unavailable' };
            const result = createRuleEngine({ preset, nextUint32: deps.nextUint32 }).resolveCheck(old.request, captured.envelope.activeSnapshot);
            const record = deps.ledger.reroll(old, { kind: 'check', branchId: captured.ref.branchId, signature: old.signature, request: structuredClone(old.request), result });
            const committed = await deps.store.commitCurrentBranchAudit({ chatId: captured.context.chatId, expectedHeadRevision: captured.envelope.headRevision, activeRef: captured.ref, record }); if (committed.ok) deps.ledger.commit([record]); return committed;
        }),
        applyDamage: input => transaction('damage', async captured => {
            const preset = deps.preset(captured.envelope.preset.id); if (!preset?.readActor || !preset?.writeActor) return { ok: false, reason: 'rules-unavailable' };
            const resolved = createRuleEngine({ preset, nextUint32: deps.nextUint32 }).applyDamage(input, captured.envelope.activeSnapshot); resolved.state.version = captured.envelope.stateVersion + 1;
            const valid = deps.validateState(captured.envelope.preset.id, resolved.state); if (!valid.ok) return { ok: false, reason: 'invalid-state', errors: valid.errors };
            const record = deps.ledger.createRecord({ kind: 'damage', branchId: captured.ref.branchId, request: structuredClone(input), result: structuredClone(resolved.damage) });
            const committed = await deps.store.commitCurrentBranchMutation({ chatId: captured.context.chatId, expectedHeadRevision: captured.envelope.headRevision, baseVersion: captured.envelope.stateVersion, activeRef: captured.ref, nextState: resolved.state, patch: { operations: [] }, source: 'manual-damage', record }); if (committed.ok) deps.ledger.commit([record]); return committed;
        }),
        resummarize: async () => {
            const captured = current(deps); if (!captured) return { ok: false, reason: 'not-writable' };
            const candidate = await deps.modelService.requestSummary({ profileId: deps.config().recorderProfileId, presetId: captured.envelope.preset.id, state: captured.envelope.activeSnapshot, oldState: captured.envelope.activeSnapshot, baseVersion: captured.envelope.stateVersion, version: captured.envelope.stateVersion + 1 }); candidate.state.version = captured.envelope.stateVersion + 1;
            const valid = deps.validateState(captured.envelope.preset.id, candidate.state); if (!valid.ok) return { ok: false, reason: 'invalid-state', errors: valid.errors };
            if (!await deps.confirm({ action: 'resummarize', candidate: safeText(candidate.state) })) return { ok: false, reason: 'cancelled' };
            return deps.queue.enqueue(captured.context.chatId, `resummarize-${deps.makeId()}`, async signal => { signal.throwIfAborted(); if (!same(deps, captured)) return { ok: false, reason: 'stale' }; return deps.store.commitCurrentBranchMutation({ chatId: captured.context.chatId, expectedHeadRevision: captured.headRevision, baseVersion: captured.stateVersion, activeRef: captured.ref, nextState: candidate.state, patch: { operations: [] }, source: 'resummarize' }); });
        },
        importPreset: async () => { const file = await deps.pickFile?.(); if (!file) return { ok: false, reason: 'cancelled' }; try { return await deps.presetManager.importPreset(await file.text()); } catch (error) { return { ok: false, reason: 'invalid-preset', error: safeText(error) }; } },
        exportPreset: id => { const preset = deps.presetManager.exportPreset(id); download(`dualmodel-preset-${id}.json`, preset); return { ok: true }; },
        exportRaw: () => { const env = deps.store.loadEnvelope?.().value; const raw = { schemaVersion: env?.schemaVersion, preset: env?.preset, stateVersion: env?.stateVersion, activeSnapshot: env?.activeSnapshot, activeRef: env?.activeRef, records: deps.ledger.list() }; download('dualmodel-raw.json', raw); return { ok: true }; },
    };
}
