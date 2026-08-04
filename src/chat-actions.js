import { createRuleEngine } from './rule-engine.js';

function safeText(value) { try { return typeof value === 'string' ? value : JSON.stringify(value); } catch { return String(value); } }
function envelopeOf(deps) { const loaded = deps.store.loadEnvelope?.(); return loaded?.value ?? loaded; }
function current(deps) {
    const context = deps.adapter.getContext?.(); const envelope = envelopeOf(deps);
    if (!context?.chatId || context.groupId || !deps.config?.().enabled || deps.orchestrator?.getActiveGeneration?.() || !envelope?.activeRef) return null;
    return { context, envelope, ref: structuredClone(envelope.activeRef), chat: context.chat, metadata: context.chatMetadata, headRevision: envelope.headRevision, stateVersion: envelope.stateVersion, preset: structuredClone(envelope.preset), messages: structuredClone(context.chat ?? []) };
}
function same(deps, captured) {
    const context = deps.adapter.getContext?.(); const envelope = envelopeOf(deps);
    return Boolean(context?.chatId === captured.context.chatId && context.chat === captured.chat && context.chatMetadata === captured.metadata && envelope === captured.envelope && envelope.headRevision === captured.headRevision && envelope.stateVersion === captured.stateVersion && JSON.stringify(envelope.activeRef) === JSON.stringify(captured.ref) && JSON.stringify(envelope.preset) === JSON.stringify(captured.preset));
}
function browserDownload(name, value) {
    const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    const url = globalThis.URL.createObjectURL(new globalThis.Blob([text], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); globalThis.queueMicrotask(() => globalThis.URL.revokeObjectURL(url));
}
function validateDamage(deps, input) {
    if (typeof deps.validateDamage === 'function') return deps.validateDamage(input);
    const ok = Boolean(input && typeof input.target === 'string' && input.target.trim() && typeof input.expression === 'string' && input.expression.trim());
    return ok ? { ok: true, errors: [] } : { ok: false, errors: [{ message: 'Damage target and expression are required' }] };
}
function rawExport(envelope, records) {
    if (!envelope) return { schemaVersion: undefined, preset: undefined, stateVersion: undefined, activeSnapshot: undefined, activeRef: undefined, records };
    // This is deliberately a whitelist: profile IDs and any future credentials stay local.
    return { schemaVersion: envelope.schemaVersion, preset: structuredClone(envelope.preset), stateVersion: envelope.stateVersion, activeSnapshot: structuredClone(envelope.activeSnapshot), activeRef: structuredClone(envelope.activeRef), records: structuredClone(records) };
}

export function createChatActions(deps) {
    const download = deps.download ?? browserDownload;
    async function transaction(label, work) {
        const captured = current(deps); if (!captured) return { ok: false, reason: 'not-writable' };
        return deps.queue.enqueue(captured.context.chatId, `${label}-${deps.makeId()}`, async signal => { signal.throwIfAborted(); if (!same(deps, captured)) return { ok: false, reason: 'stale' }; return work(captured, signal); });
    }
    return {
        recalculate: async () => {
            const captured = current(deps); if (!captured) return { ok: false, reason: 'not-writable' };
            const index = deps.currentInvalidIndex?.(); const plan = deps.rollbackManager.buildRecalculationPlan?.(index);
            if (!plan) return { ok: false, reason: 'no-recalculation-boundary' };
            if (!await deps.confirm({ action: 'recalculate', plan })) return { ok: false, reason: 'cancelled' };
            return deps.rollbackManager.recalculate(index);
        },
        reroll: () => transaction('reroll', async captured => {
            const records = deps.ledger.list(); const old = deps.selectedCheck?.() ?? records.findLast(record => record.kind === 'check' && record.branchId === captured.ref.branchId);
            if (!old?.request || old.kind !== 'check') return { ok: false, reason: 'missing-check' };
            const preset = deps.preset(captured.preset.id); if (!preset?.readActor || !preset?.writeActor) return { ok: false, reason: 'rules-unavailable' };
            let result;
            try { result = createRuleEngine({ preset, nextUint32: deps.nextUint32 }).resolveCheck(old.request, captured.envelope.activeSnapshot); }
            catch (error) { return { ok: false, reason: 'invalid-check', errors: [{ message: safeText(error?.message ?? error) }] }; }
            const record = deps.ledger.reroll(old, { kind: 'check', branchId: captured.ref.branchId, signature: old.signature, request: structuredClone(old.request), result });
            const committed = await deps.store.commitCurrentBranchAudit({ chatId: captured.context.chatId, expectedHeadRevision: captured.headRevision, activeRef: captured.ref, record });
            if (committed?.ok) deps.ledger.commit([committed.record ?? record]); return committed;
        }),
        applyDamage: async input => {
            const inputValidation = validateDamage(deps, input);
            if (!inputValidation?.ok) return { ok: false, reason: 'invalid-damage', errors: inputValidation?.errors ?? [] };
            return transaction('damage', async captured => {
                const preset = deps.preset(captured.preset.id); if (!preset?.readActor || !preset?.writeActor) return { ok: false, reason: 'rules-unavailable' };
                let resolved;
                try { resolved = createRuleEngine({ preset, nextUint32: deps.nextUint32 }).applyDamage(input, captured.envelope.activeSnapshot); }
                catch (error) { return { ok: false, reason: 'invalid-damage', errors: [{ message: safeText(error?.message ?? error) }] }; }
                resolved.state.version = captured.stateVersion + 1;
                const valid = deps.validateState(captured.preset.id, resolved.state); if (!valid.ok) return { ok: false, reason: 'invalid-state', errors: valid.errors };
                const record = deps.ledger.createRecord({ kind: 'damage', branchId: captured.ref.branchId, request: structuredClone(input), result: structuredClone(resolved.damage) });
                const committed = await deps.store.commitCurrentBranchMutation({ chatId: captured.context.chatId, expectedHeadRevision: captured.headRevision, baseVersion: captured.stateVersion, activeRef: captured.ref, nextState: resolved.state, patch: { operations: [] }, source: 'manual-damage', record });
                if (committed?.ok) deps.ledger.commit([committed.record ?? record]); return committed;
            });
        },
        resummarize: async () => {
            const captured = current(deps); if (!captured) return { ok: false, reason: 'not-writable' };
            let candidate;
            try { candidate = await deps.modelService.requestSummary({ profileId: deps.config().recorderProfileId, presetId: captured.preset.id, messages: captured.messages, version: captured.stateVersion + 1 }); }
            catch (error) { return { ok: false, reason: 'summary-failed', errors: [{ message: safeText(error?.message ?? error) }] }; }
            if (!candidate?.state || typeof candidate.state !== 'object' || Array.isArray(candidate.state)) return { ok: false, reason: 'invalid-state', errors: [{ message: 'Summary did not return a state object' }] };
            candidate = structuredClone(candidate.state); candidate.version = captured.stateVersion + 1;
            const valid = deps.validateState(captured.preset.id, candidate); if (!valid.ok) return { ok: false, reason: 'invalid-state', errors: valid.errors };
            if (!await deps.confirm({ action: 'resummarize', candidate: safeText(candidate) })) return { ok: false, reason: 'cancelled' };
            return deps.queue.enqueue(captured.context.chatId, `resummarize-${deps.makeId()}`, async signal => { signal.throwIfAborted(); if (!same(deps, captured)) return { ok: false, reason: 'stale' }; return deps.store.commitCurrentBranchMutation({ chatId: captured.context.chatId, expectedHeadRevision: captured.headRevision, baseVersion: captured.stateVersion, activeRef: captured.ref, nextState: candidate, patch: { operations: [] }, source: 'resummarize' }); });
        },
        importPreset: async () => { try { const file = await deps.pickFile?.(); if (!file) return { ok: false, reason: 'cancelled' }; return await deps.presetManager.importPreset(await file.text()); } catch (error) { return { ok: false, reason: 'invalid-preset', error: safeText(error?.message ?? error) }; } },
        exportPreset: async id => { try { const preset = await deps.presetManager.exportPreset(id); await download(`dualmodel-preset-${id}.json`, preset); return { ok: true }; } catch (error) { return { ok: false, reason: 'export-failed', error: safeText(error?.message ?? error) }; } },
        exportRaw: async () => { try { const envelope = envelopeOf(deps); await download('dualmodel-raw.json', rawExport(envelope, deps.ledger.list())); return { ok: true }; } catch (error) { return { ok: false, reason: 'export-failed', error: safeText(error?.message ?? error) }; } },
    };
}
