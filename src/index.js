export { default as Ajv } from 'ajv';
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
import { resolveConfig } from './config-resolver.js';
import { ensureMessageId, hashText } from './identity.js';

export { createOrchestrator } from './orchestrator.js';

export async function bootstrap({ adapter, dependencies } = {}) {
    const runtimeAdapter = adapter ?? (await import('./st-runtime.js')).createRuntimeAdapter();
    const presets = [narrativePreset];
    const validator = createStateValidator({ presets });
    const resolved = dependencies ?? {};
    const store = resolved.store ?? createStateStore({ adapter: runtimeAdapter, hashText });
    const modelService = resolved.modelService ?? createModelService({ adapter: runtimeAdapter, validatePatch: validator.validatePatch, validateState: validator.validateState });
    const queue = resolved.queue ?? createChatTaskQueue();
    const getEffectiveConfig = resolved.getConfig ?? (() => {
        const envelope = store.loadEnvelope?.();
        return resolveConfig({ globalConfig: runtimeAdapter.getSettings?.(), chatConfig: envelope?.ok ? envelope.value.configOverrides : envelope?.configOverrides });
    });
    let orchestrator;
    const rollbackManager = resolved.rollbackManager ?? createRollbackManager({
        adapter: runtimeAdapter, store, queue,
        confirm: resolved.confirmRecalculation ?? (async () => false),
        replayTurn: resolved.replayTurn ?? (input => orchestrator?.replayTurn(input) ?? Promise.resolve({ ok: false, reason: 'replay-unavailable' })),
        isWritable: resolved.isWritable ?? (() => { const current = runtimeAdapter.getContext(); return !current.groupId && Boolean(getEffectiveConfig().enabled); }),
    });
    orchestrator = createOrchestrator({
        adapter: runtimeAdapter, store, validator, modelService,
        promptInjector: resolved.promptInjector ?? createPromptInjector({ adapter: runtimeAdapter }), queue, rollbackManager,
        getConfig: getEffectiveConfig, getPreset: resolved.getPreset ?? (id => presets.find(item => item.id === id)),
        hasProfile: resolved.hasProfile ?? (id => runtimeAdapter.listProfiles().some(profile => profile.id === id)),
        ensureMessageId, applyPatch: applyValidatedPatch, getChecks: resolved.getChecks ?? (() => []), recordDiagnostic: resolved.recordDiagnostic ?? (() => {}), prepareSwipeGeneration: resolved.prepareSwipeGeneration ?? (input => store.prepareSwipeGeneration(input)),
        formatReusableChecks: resolved.formatReusableChecks ?? (records => records.length ? `Authoritative completed checks; do not request them again: ${records.map(record => `${record.checkId}=${record.pass ?? record.outcome ?? 'recorded'}`).join(', ')}` : ''),
    });
    orchestrator.start();
    rollbackManager.bind();
    try {
        await orchestrator.initializeChat();
    } catch (error) {
        try { orchestrator.stop(); } catch {
            // Initialization failure remains the observable root cause.
        }
        throw error;
    }

    return {
        name: 'dualModelEngine',
        adapter: runtimeAdapter,
        capabilities: probeHostCapabilities(runtimeAdapter),
        orchestrator,
    };
}

if (typeof document !== 'undefined' && import.meta.url.includes('/scripts/extensions/')) {
    void bootstrap();
}
