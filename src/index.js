import Ajv from 'ajv';
export { Ajv };
import { probeHostCapabilities } from './capability-probe.js';
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
import d20Schema from '../schemas/d20.schema.json';
import adjudicatorSchema from '../schemas/adjudicator.schema.json';
import { NAMESPACE } from './constants.js';

export { createOrchestrator } from './orchestrator.js';

export async function bootstrap({ adapter, dependencies } = {}) {
    const runtimeAdapter = adapter ?? (await import('./st-runtime.js')).createRuntimeAdapter();
    const presets = [narrativePreset, d20LitePreset];
    const validator = createStateValidator({ presets });
    const resolved = dependencies ?? {};
    const store = resolved.store ?? createStateStore({ adapter: runtimeAdapter, hashText });
    const decisionAjv = new Ajv({ allErrors: true, strict: false });
    const decisionValidator = decisionAjv.compile({ $ref: '#/$defs/decision', ...adjudicatorSchema });
    const modelService = resolved.modelService ?? createModelService({ adapter: runtimeAdapter, validatePatch: validator.validatePatch, validateState: validator.validateState, validateDecision: value => ({ ok: Boolean(decisionValidator(value)), errors: structuredClone(decisionValidator.errors ?? []) }) });
    const queue = resolved.queue ?? createChatTaskQueue();
    const getEffectiveConfig = resolved.getConfig ?? (() => {
        const envelope = store.loadEnvelope?.();
        return resolveConfig({ globalConfig: runtimeAdapter.getSettings?.(), chatConfig: envelope?.ok ? envelope.value.configOverrides : envelope?.configOverrides });
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
        const context = runtimeAdapter.getContext(); const config = getEffectiveConfig();
        const envelope = store.loadEnvelope?.(); const value = envelope?.ok ? envelope.value : envelope;
        if (context?.groupId || !config.enabled || config.rulePresetId !== 'd20-lite' || config.adjudication !== 'manual' || value?.preset?.id !== 'd20-lite') throw new Error('Manual D20 checks are not authorized for this chat configuration');
        const ref = value.activeRef; const message = context?.chat?.find(item => item?.extra?.[NAMESPACE]?.messageId === ref?.messageId);
        const branch = message?.swipe_info?.[ref?.swipeId]?.extra?.[NAMESPACE]?.branch;
        if (!ref || !message || (message.swipe_id ?? 0) !== ref.swipeId || !branch || branch.branchId !== ref.branchId || branch.status !== 'committed' || !branch.segments?.length) throw new Error('No active committed branch');
        const manualGeneration = { branchId: ref.branchId, baseBranchId: ref.branchId, userMessageId: branch.segments.at(-1).userMessageId ?? null, baseSnapshot: structuredClone(value.activeSnapshot), pendingRuleRecords: [], pendingRuleEffects: [], ruleReplayMode: null, closed: false };
        const record = await stageCheckRecord({ generation: manualGeneration, input, ledger, resolveCheck: (request, state) => createRuleEngine({ nextUint32, preset: d20LitePreset }).resolveCheck(request, state) });
        const committed = await store.commitCurrentBranchAudit({ chatId: context.chatId, expectedHeadRevision: value.headRevision, activeRef: ref, record });
        if (!committed?.ok) throw new Error(committed?.reason ?? 'manual audit failed'); ledger.commit([committed.record ?? record]); return committed.record ?? record;
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
        getConfig: getEffectiveConfig, getPreset: resolved.getPreset ?? (id => presets.find(item => item.id === id)),
        hasProfile: resolved.hasProfile ?? (id => runtimeAdapter.listProfiles().some(profile => profile.id === id)),
        ensureMessageId, applyPatch: applyValidatedPatch, getChecks: resolved.getChecks ?? (() => []), recordDiagnostic: resolved.recordDiagnostic ?? (() => {}), prepareSwipeGeneration: resolved.prepareSwipeGeneration ?? (input => store.prepareSwipeGeneration(input)),
        formatReusableChecks: resolved.formatReusableChecks ?? (records => records.length ? `Authoritative completed checks; do not request them again: ${records.map(record => `${record.checkId}=${record.pass ?? record.outcome ?? 'recorded'}`).join(', ')}` : ''), adjudicator,
    });
    const toolRegistry = resolved.toolRegistry ?? createToolRegistry({
        adapter: runtimeAdapter, getConfig: getEffectiveConfig, getActiveGeneration: () => orchestrator?.getActiveGeneration(), validateCheck: validate(checkValidator), validateDamage: validate(damageValidator), ledger,
        resolveCheck,
        resolveDamage: async (input, state) => { const engine = createRuleEngine({ nextUint32, preset: orchestrator.getActiveGeneration()?.preset ?? d20LitePreset }); const hpBefore = state.actors?.[input.target]?.hp?.current; const result = engine.applyDamage(input, state); return { state: result.state, audit: { rolls: result.damage.rolls, raw: result.damage.rawTotal, total: result.damage.total, absorbed: result.damage.absorbed, hpBefore, hpAfter: result.state.actors?.[input.target]?.hp?.current } }; },
    });
    const canRegisterTools = typeof runtimeAdapter.registerTool === 'function';
    try {
        orchestrator.start();
        if (canRegisterTools) toolRegistry.register();
        rollbackManager.bind();
        await orchestrator.initializeChat();
    } catch (error) {
        try { if (canRegisterTools) toolRegistry.unregister(); } catch {
            // Initialization failure remains the observable root cause.
        }
        try { orchestrator.stop(); } catch {
            // Initialization failure remains the observable root cause.
        }
        throw error;
    }
    const stopOrchestrator = orchestrator.stop.bind(orchestrator); let orchestratorStopped = false;
    orchestrator.stop = () => { let first; if (canRegisterTools) try { toolRegistry.unregister(); } catch (error) { first = error; } if (!orchestratorStopped) try { stopOrchestrator(); orchestratorStopped = true; } catch (error) { first ??= error; } if (first) throw first; };

    return {
        name: 'dualModelEngine',
        adapter: runtimeAdapter,
        capabilities: probeHostCapabilities(runtimeAdapter),
        orchestrator,
        ledger,
        toolRegistry,
        adjudicator,
    };
}

if (typeof document !== 'undefined' && import.meta.url.includes('/scripts/extensions/')) {
    void bootstrap();
}
