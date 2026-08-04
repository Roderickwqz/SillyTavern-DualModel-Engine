import Ajv from 'ajv';
export { Ajv };
import { probeHostCapabilities, runDynamicToolProbe } from './capability-probe.js';
import { createOrchestrator } from './orchestrator.js';
import { createStateStore } from './state-store.js';
import { createPromptInjector } from './prompt-injector.js';
import { createModelService } from './model-service.js';
import { createStateValidator } from './state-validator.js';
import { applyValidatedPatch } from './json-patch.js';
import { createChatTaskQueue } from './task-queue.js';
import { createRollbackManager } from './rollback-manager.js';
import { narrativePreset } from './rules/narrative.js';
import { d20LitePreset } from './rules/d20-lite.js';
import { resolveConfig } from './config-resolver.js';
import { ensureMessageId, hashText } from './identity.js';
import { createCheckLedger } from './check-ledger.js';
import { createToolRegistry, stageCheckRecord } from './tool-registry.js';
import { createAdjudicatorService } from './adjudicator-service.js';
import { createRuleEngine } from './rule-engine.js';
import { createWebCryptoUint32 } from './dice-engine.js';
import { createPresetManager } from './preset-manager.js';
import { createUIController } from './ui/controller.js';
import { createChatActions } from './chat-actions.js';
import d20Schema from '../schemas/d20.schema.json';
import adjudicatorSchema from '../schemas/adjudicator.schema.json';
import { NAMESPACE } from './constants.js';

export { createOrchestrator } from './orchestrator.js';

function pointer(part) { return String(part).replaceAll('~', '~0').replaceAll('/', '~1'); }
function diffState(before, after, path = '') {
    if (JSON.stringify(before) === JSON.stringify(after)) return [];
    if (!before || !after || typeof before !== 'object' || typeof after !== 'object' || Array.isArray(before) || Array.isArray(after)) return [{ op: before === undefined ? 'add' : after === undefined ? 'remove' : 'replace', path, ...(after === undefined ? {} : { value: structuredClone(after) }) }];
    const operations = [];
    for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) operations.push(...diffState(before[key], after[key], `${path}/${pointer(key)}`));
    return operations;
}
function pickPresetFile() {
    if (typeof document === 'undefined' || !document.body) return Promise.resolve(null);
    return new Promise(resolve => {
        const input = document.createElement('input'); input.type = 'file'; input.accept = 'application/json,.json'; input.hidden = true;
        const finish = value => { input.remove(); resolve(value); };
        input.addEventListener('change', () => finish(input.files?.[0] ?? null), { once: true });
        input.addEventListener('cancel', () => finish(null), { once: true });
        document.body.append(input); input.click();
    });
}
function firstInvalidHistoryIndex(adapter) {
    const chat = adapter.getContext?.()?.chat ?? [];
    return chat.findIndex(message => { const swipe = message.swipe_info?.[message.swipe_id ?? 0];
        const branch = swipe?.extra?.[NAMESPACE]?.branch;
        return ['stale', 'invalidated', 'failed'].includes(branch?.status) || (branch?.segments ?? []).some(segment => ['stale', 'invalidated', 'failed'].includes(segment?.status));
    });
}

export async function bootstrap({ adapter, dependencies } = {}) {
    const runtimeAdapter = adapter ?? (await import('./st-runtime.js')).createRuntimeAdapter();
    const resolved = dependencies ?? {};
    const recordDiagnostic = resolved.recordDiagnostic ?? (async value => {
        const settings = runtimeAdapter.getSettings?.(); if (!settings || typeof settings !== 'object') return;
        const diagnostics = Array.isArray(settings.diagnostics) ? settings.diagnostics : [];
        settings.diagnostics = [...diagnostics, structuredClone(value)].slice(-100);
        await (runtimeAdapter.saveSettings?.() ?? runtimeAdapter.saveGlobalSettings?.());
    });
    const presets = [narrativePreset, d20LitePreset];
    const validator = createStateValidator({ presets });
    const store = resolved.store ?? createStateStore({ adapter: runtimeAdapter, hashText });
    const settings = runtimeAdapter.getSettings?.() ?? {};
    const presetManager = resolved.presetManager ?? createPresetManager({
        settings, builtInPresets: presets, registerPreset: validator.registerPreset, unregisterPreset: validator.unregisterPreset,
        save: () => runtimeAdapter.saveSettings?.(),
        getReferences: id => {
            const context = runtimeAdapter.getContext?.();
            const references = [];
            if (context?.chatMetadata?.[NAMESPACE]?.preset?.id === id) references.push({ type: 'chat', id: context.chatId });
            const character = context?.character ?? context?.characters?.[context?.characterId];
            if (character?.data?.extensions?.[NAMESPACE]?.rulePresetId === id) references.push({ type: 'character', id: context?.characterId ?? character.id ?? 'current' });
            const persisted = runtimeAdapter.listPresetReferences?.(id);
            if (Array.isArray(persisted)) references.push(...persisted);
            return references;
        }, stateStore: store,
    });
    const decisionAjv = new Ajv({ allErrors: true, strict: false });
    const decisionValidator = decisionAjv.compile({ $ref: '#/$defs/decision', ...adjudicatorSchema });
    const modelService = resolved.modelService ?? createModelService({ adapter: runtimeAdapter, validatePatch: (patch, input) => validator.validatePatch(input?.presetId, patch, input?.policy), validateState: (state, input) => validator.validateState(input?.presetId, state), validateDecision: value => ({ ok: Boolean(decisionValidator(value)), errors: structuredClone(decisionValidator.errors ?? []) }) });
    const queue = resolved.queue ?? createChatTaskQueue();
    const getEffectiveConfig = resolved.getConfig ?? (() => {
        const envelope = store.loadEnvelope?.();
        const context = runtimeAdapter.getContext?.(); const character = runtimeAdapter.getCurrentCharacter?.() ?? context?.characters?.[context?.characterId] ?? context?.character;
        return resolveConfig({ globalConfig: runtimeAdapter.getSettings?.(), characterConfig: character?.data?.extensions?.[NAMESPACE], chatConfig: envelope?.ok ? envelope.value.configOverrides : envelope?.configOverrides });
    });
    const makeId = resolved.makeId ?? (() => { if (typeof globalThis.crypto?.randomUUID !== 'function') throw new Error('Web Crypto randomUUID is unavailable'); return globalThis.crypto.randomUUID(); });
    const ledger = resolved.ledger ?? createCheckLedger({ makeId, now: resolved.now ?? (() => new Date().toISOString()), initialRecords: store.listRuleRecords?.() ?? [] });
    const ajv = new Ajv({ allErrors: true, strict: false });
    const checkValidator = ajv.compile({ $ref: '#/$defs/checkInput', ...d20Schema });
    const damageValidator = ajv.compile({ $ref: '#/$defs/damageInput', ...d20Schema });
    const validate = fn => input => ({ ok: Boolean(fn(input)), errors: structuredClone(fn.errors ?? []) });
    let randomSource;
    const nextUint32 = resolved.nextUint32 ?? (() => (randomSource ??= createWebCryptoUint32(globalThis.crypto))());
    let orchestrator;
    const resolveCheck = async (input, state) => createRuleEngine({ nextUint32, preset: orchestrator?.getActiveGeneration()?.preset ?? d20LitePreset }).resolveCheck(input, state);
    const resolveManualCheck = resolved.resolveManualCheck ?? (async input => {
        const queuedChatId = runtimeAdapter.getContext()?.chatId;
        if (!queuedChatId) throw new Error('No active chat for manual D20 check');
        if (orchestrator?.getActiveGeneration()) throw new Error('Finish generation before resolving a manual D20 check');
        return queue.enqueue(queuedChatId, `manual-${makeId()}`, async signal => {
            signal.throwIfAborted();
            if (orchestrator?.getActiveGeneration()) throw new Error('Finish generation before resolving a manual D20 check');
            const context = runtimeAdapter.getContext(); const config = getEffectiveConfig();
            const envelope = store.loadEnvelope?.(); const value = envelope?.ok ? envelope.value : envelope;
            let activePreset; try { activePreset = presetManager.getPreset(value?.preset?.id); } catch { activePreset = null; }
            if (context?.chatId !== queuedChatId || context?.groupId || !config.enabled || config.rulePresetId !== value?.preset?.id || config.adjudication !== 'manual' || typeof activePreset?.readActor !== 'function' || typeof activePreset?.writeActor !== 'function') throw new Error('Manual D20 checks are not authorized for this chat configuration');
            const ref = value.activeRef; const message = context.chat?.find(item => item?.extra?.[NAMESPACE]?.messageId === ref?.messageId);
            const branch = message?.swipe_info?.[ref?.swipeId]?.extra?.[NAMESPACE]?.branch;
            if (!ref || !message || (message.swipe_id ?? 0) !== ref.swipeId || !branch || branch.branchId !== ref.branchId || branch.status !== 'committed' || !branch.segments?.length) throw new Error('No active committed branch');
            const manualGeneration = { branchId: ref.branchId, baseBranchId: ref.branchId, userMessageId: branch.segments.at(-1).userMessageId ?? null, baseSnapshot: structuredClone(value.activeSnapshot), pendingRuleRecords: [], pendingRuleEffects: [], ruleReplayMode: null, closed: false };
            const record = await stageCheckRecord({ generation: manualGeneration, input, ledger, signal, resolveCheck: (request, state) => createRuleEngine({ nextUint32, preset: activePreset }).resolveCheck(request, state) });
            signal.throwIfAborted();
            const committed = await store.commitCurrentBranchAudit({ chatId: context.chatId, expectedHeadRevision: value.headRevision, activeRef: ref, record });
            if (!committed?.ok) throw new Error(committed?.reason ?? 'manual audit failed'); ledger.commit([committed.record ?? record]); return committed.record ?? record;
        });
    });
    const adjudicator = resolved.adjudicator ?? createAdjudicatorService({ toolProbe: resolved.toolProbe ?? { supported: false }, getToolProbe: resolved.getToolProbe ?? (() => runtimeAdapter.getSettings?.().toolProbe ?? { supported: false }), getMainApiModelLabel: () => runtimeAdapter.getMainApiModelLabel?.(), requestDecision: resolved.requestDecision ?? (input => modelService.requestDecision({ ...input, profileId: input.recorderProfileId ?? getEffectiveConfig().recorderProfileId })), validateInput: validate(checkValidator), stageCheck: async (input, context) => { const generation = context.generation ?? orchestrator?.getActiveGeneration(); if (!generation) throw new Error('No active generation'); return stageCheckRecord({ generation, input, ledger, resolveCheck, signal: context.signal, isActive: () => orchestrator?.getActiveGeneration() === generation && !generation.closed }); }, formatCheck: resolved.formatCheck ?? (check => `Formal check ${check.checkId}: total ${check.result.total} vs DC ${check.result.dc} — ${check.result.outcome}`), confirm: resolved.confirm ?? (async () => true), resolveManualCheck });
    const rollbackManager = resolved.rollbackManager ?? createRollbackManager({
        adapter: runtimeAdapter, store, queue,
        confirm: resolved.confirmRecalculation ?? (async () => false),
        replayTurn: resolved.replayTurn ?? (input => orchestrator?.replayTurn(input) ?? Promise.resolve({ ok: false, reason: 'replay-unavailable' })),
        isWritable: resolved.isWritable ?? (() => { const current = runtimeAdapter.getContext(); return !current.groupId && Boolean(getEffectiveConfig().enabled); }),
    });
    orchestrator = createOrchestrator({
        adapter: runtimeAdapter, store, validator, modelService, ledger,
        promptInjector: resolved.promptInjector ?? createPromptInjector({ adapter: runtimeAdapter }), queue, rollbackManager,
        getConfig: getEffectiveConfig, getPreset: resolved.getPreset ?? (id => presetManager.getPreset(id)),
        hasProfile: resolved.hasProfile ?? (id => runtimeAdapter.listProfiles().some(profile => profile.id === id)),
        ensureMessageId, applyPatch: applyValidatedPatch, getChecks: resolved.getChecks ?? (() => []), prepareSwipeGeneration: resolved.prepareSwipeGeneration ?? (input => store.prepareSwipeGeneration(input)),
        formatReusableChecks: resolved.formatReusableChecks ?? (records => records.length ? `Authoritative completed checks; do not request them again: ${records.map(record => `${record.checkId}=${record.pass ?? record.outcome ?? 'recorded'}`).join(', ')}` : ''), adjudicator, recordDiagnostic,
    });
    const toolRegistry = resolved.toolRegistry ?? createToolRegistry({
        adapter: runtimeAdapter, getConfig: getEffectiveConfig, getActiveGeneration: () => orchestrator?.getActiveGeneration(), validateCheck: validate(checkValidator), validateDamage: validate(damageValidator), ledger,
        resolveCheck,
        resolveDamage: async (input, state) => { const preset = orchestrator.getActiveGeneration()?.preset ?? d20LitePreset; const engine = createRuleEngine({ nextUint32, preset }); const hpBefore = preset.readActor(state, input.target)?.hp?.current; const result = engine.applyDamage(input, state); return { state: result.state, audit: { rolls: result.damage.rolls, raw: result.damage.rawTotal, total: result.damage.total, absorbed: result.damage.absorbed, hpBefore, hpAfter: preset.readActor(result.state, input.target)?.hp?.current } }; },
    });
    const canRegisterTools = typeof runtimeAdapter.registerTool === 'function';
    const confirmAction = resolved.showConfirm ?? (async details => { const context = runtimeAdapter.getContext?.(); if (typeof context?.Popup !== 'function') return globalThis.window?.confirm(details.message ?? details.content?.textContent ?? 'Confirm') ?? false; const content = details.content instanceof globalThis.HTMLElement ? details.content : Object.assign(document.createElement('div'), { textContent: details.message ?? JSON.stringify(details) }); return (await new context.Popup(content, context.POPUP_TYPE?.CONFIRM, '', {}).show()) === context.POPUP_RESULT?.AFFIRMATIVE; });
    const currentInvalidIndex = resolved.currentInvalidIndex ?? (() => { const index = firstInvalidHistoryIndex(runtimeAdapter); return index < 0 ? undefined : index; });
    let selectedCheckId = null;
    const selectedCheck = resolved.selectedCheck ?? (() => { const ref = store.loadEnvelope?.().value?.activeRef; return ledger.list().find(record => record.checkId === selectedCheckId && record.branchId === ref?.branchId) ?? null; });
    const chatActions = createChatActions({ adapter: runtimeAdapter, store, queue, ledger, modelService, presetManager, orchestrator, makeId, nextUint32, preset: id => presetManager.getPreset(id), validateState: (id, state) => validator.validateState(id, state), validateDamage: validate(damageValidator), config: getEffectiveConfig, rollbackManager, confirm: confirmAction, currentInvalidIndex, pickFile: resolved.pickPresetFile ?? pickPresetFile, selectedCheck, download: resolved.download, diffState });
    let ui;
    try {
        orchestrator.start();
        if (canRegisterTools) toolRegistry.register();
        rollbackManager.bind();
        await orchestrator.initializeChat();
        ui = createUIController({
            adapter: runtimeAdapter, queue, capabilities: probeHostCapabilities(runtimeAdapter), presetManager,
            getGlobalConfig: () => runtimeAdapter.getGlobalSettings?.() ?? runtimeAdapter.getSettings?.() ?? {},
            getCharacterConfig: () => { const character = runtimeAdapter.getCurrentCharacter?.(); return character?.data?.extensions?.[NAMESPACE] ?? {}; },
            getChatConfig: () => runtimeAdapter.getChatMetadata?.()?.[NAMESPACE]?.configOverrides ?? {},
            saveGlobalConfig: value => runtimeAdapter.saveGlobalSettings?.(value) ?? runtimeAdapter.saveSettings?.(),
            saveCharacterConfig: value => runtimeAdapter.saveCurrentCharacter?.(value),
            saveChatConfig: (value, identity) => runtimeAdapter.saveChatSettings?.(value, identity),
            listProfiles: () => runtimeAdapter.listProfiles?.() ?? [], listPresets: () => presetManager.listPresets(),
            getEnvelope: () => store.loadEnvelope?.().value ?? runtimeAdapter.getContext?.()?.chatMetadata?.[NAMESPACE],
            validateState: state => { const envelope = store.loadEnvelope?.().value ?? runtimeAdapter.getContext?.()?.chatMetadata?.[NAMESPACE]; return validator.validateState(envelope?.preset?.id, state); },
            diffState,
            getPresetPolicy: () => { const envelope = store.loadEnvelope?.().value ?? runtimeAdapter.getContext?.()?.chatMetadata?.[NAMESPACE]; const preset = presetManager.getPreset(envelope?.preset?.id); return { allowedPaths: preset?.allowedPaths ?? [], lockedPaths: preset?.lockedPaths?.filter(path => path !== '/version') ?? [], ruleLockedPaths: preset?.ruleLockedPaths ?? [] }; },
            getPresetUiFields: () => { const envelope = store.loadEnvelope?.().value ?? runtimeAdapter.getContext?.()?.chatMetadata?.[NAMESPACE]; return presetManager.getPreset(envelope?.preset?.id)?.ui ?? []; },
            listChecks: () => { const ref = (store.loadEnvelope?.().value ?? runtimeAdapter.getContext?.()?.chatMetadata?.[NAMESPACE])?.activeRef; return ref ? ledger.list().filter(record => record.branchId === ref.branchId) : []; },
            onSelectCheck: id => { selectedCheckId = id; },
            listHistory: () => (runtimeAdapter.getContext?.()?.chat ?? []).flatMap(message => (message.swipe_info ?? []).flatMap(swipe => { const branch = swipe?.extra?.[NAMESPACE]?.branch; return (branch?.segments ?? []).map(segment => ({ ...segment, status: branch.status ?? segment.status ?? 'committed' })); })),
            listDiagnostics: () => { const settings = runtimeAdapter.getSettings?.() ?? {}; return settings.diagnostics ?? settings[NAMESPACE]?.diagnostics ?? []; },
            commitManualPatch: async input => {
                const context = runtimeAdapter.getContext?.(); const envelope = store.loadEnvelope?.().value;
                if (!context?.chatId || context.groupId || orchestrator.getActiveGeneration?.()) return { ok: false, reason: 'not-writable' };
                const message = context.chat?.find(item => item?.extra?.[NAMESPACE]?.messageId === envelope?.activeRef?.messageId); const swipe = message?.swipe_info?.[envelope?.activeRef?.swipeId]; const branch = swipe?.extra?.[NAMESPACE]?.branch;
                const captured = { chatId: context.chatId, chat: context.chat, metadata: context.chatMetadata, envelope, preset: structuredClone(envelope?.preset), ref: structuredClone(envelope?.activeRef), head: envelope?.headRevision, message, swipe, branch };
                return queue.enqueue(captured.chatId, `editor-${makeId()}`, async signal => {
                    signal.throwIfAborted(); const latest = runtimeAdapter.getContext?.(); const value = store.loadEnvelope?.().value; const latestMessage = latest?.chat?.find(item => item?.extra?.[NAMESPACE]?.messageId === captured.ref?.messageId); const latestSwipe = latestMessage?.swipe_info?.[captured.ref?.swipeId];
                    if (latest?.groupId || orchestrator.getActiveGeneration?.() || latest?.chatId !== captured.chatId || latest.chat !== captured.chat || latest.chatMetadata !== captured.metadata || value !== captured.envelope || JSON.stringify(value?.preset) !== JSON.stringify(captured.preset) || JSON.stringify(value?.activeRef) !== JSON.stringify(captured.ref) || value?.headRevision !== captured.head || value?.stateVersion !== input.baseVersion || latestMessage !== captured.message || latestSwipe !== captured.swipe || latestSwipe?.extra?.[NAMESPACE]?.branch !== captured.branch) return { ok: false, reason: 'stale' };
                    const nextState = structuredClone(input.nextState); nextState.version = input.baseVersion + 1;
                    const valid = validator.validateState(value.preset?.id, nextState); if (!valid.ok) return { ok: false, reason: 'invalid-state', errors: valid.errors };
                    const committed = await store.commitCurrentBranchMutation({ chatId: captured.chatId, expectedHeadRevision: captured.head, baseVersion: input.baseVersion, activeRef: captured.ref, nextState, patch: { base_version: input.baseVersion, operations: input.operations }, source: input.source });
                    if (!committed?.ok) return committed;
                    const audit = await store.auditActiveRef?.(); return audit?.ok ? committed : { ok: false, reason: audit?.reason ?? 'audit-failed' };
                });
            },
            rollbackManager,
            recalculateCurrentBranch: chatActions.recalculate,
            currentInvalidIndex,
            rerollSelectedCheck: chatActions.reroll,
            applyManualDamage: input => chatActions.applyDamage(input),
            resummarizeCurrentBranch: chatActions.resummarize,
            importPresetFromPicker: chatActions.importPreset,
            downloadPreset: () => chatActions.exportPreset((store.loadEnvelope?.().value ?? {}).preset?.id),
            downloadRawData: chatActions.exportRaw,
            bindCharacterPreset: async id => {
                const character = runtimeAdapter.getCurrentCharacter?.(); if (!character) throw new Error('Current character is unavailable');
                character.data ??= {}; character.data.extensions ??= {}; const had = Object.hasOwn(character.data.extensions, NAMESPACE); const before = structuredClone(character.data.extensions[NAMESPACE]);
                try { presetManager.bindCharacter(character, id); await runtimeAdapter.saveCurrentCharacter?.(character.data.extensions[NAMESPACE]); }
                catch (error) { if (had) character.data.extensions[NAMESPACE] = before; else delete character.data.extensions[NAMESPACE]; throw error; }
            },
            bindChatPreset: (id, options) => presetManager.bindChat(runtimeAdapter.getChatMetadata?.(), id, options),
            exportPreset: id => presetManager.exportPreset(id),
            onConfigChanged: () => orchestrator.initializeChat(),
            runToolProbe: resolved.runToolProbe ?? (() => runDynamicToolProbe(runtimeAdapter)),
            saveProbeResult: resolved.runToolProbe ? async result => { const value = structuredClone(runtimeAdapter.getGlobalSettings?.() ?? runtimeAdapter.getSettings?.() ?? {}); value.toolProbe = structuredClone(result); await (runtimeAdapter.saveGlobalSettings?.(value) ?? runtimeAdapter.saveSettings?.()); } : async () => {},
            showConfirm: confirmAction,
        });
        await ui.mount();
    } catch (error) {
        try { if (canRegisterTools) toolRegistry.unregister(); } catch {
            // Initialization failure remains the observable root cause.
        }
        try { orchestrator.stop(); } catch {
            // Initialization failure remains the observable root cause.
        }
        try { ui?.destroy(); } catch {
            // UI cleanup cannot replace the initialization failure.
        }
        throw error;
    }
    const stopOrchestrator = orchestrator.stop.bind(orchestrator); let orchestratorStopped = false;
    orchestrator.stop = () => { let first; try { ui?.destroy(); } catch (error) { first = error; } if (canRegisterTools) try { toolRegistry.unregister(); } catch (error) { first ??= error; } if (!orchestratorStopped) try { stopOrchestrator(); orchestratorStopped = true; } catch (error) { first ??= error; } if (first) throw first; };

    return {
        name: 'dualModelEngine',
        adapter: runtimeAdapter,
        capabilities: probeHostCapabilities(runtimeAdapter),
        orchestrator,
        ledger,
        toolRegistry,
        adjudicator,
        presetManager,
        ui,
    };
}

if (typeof document !== 'undefined' && import.meta.url.includes('/scripts/extensions/')) {
    void bootstrap();
}
