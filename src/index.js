export { default as Ajv } from 'ajv';
import { probeHostCapabilities } from './capability-probe.js';
import { createOrchestrator } from './orchestrator.js';
import { createStateStore } from './state-store.js';
import { createPromptInjector } from './prompt-injector.js';
import { createModelService } from './model-service.js';
import { createStateValidator } from './state-validator.js';
import { applyValidatedPatch } from './json-patch.js';
import { createChatTaskQueue } from './task-queue.js';
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
    const orchestrator = createOrchestrator({
        adapter: runtimeAdapter, store, validator, modelService,
        promptInjector: resolved.promptInjector ?? createPromptInjector({ adapter: runtimeAdapter }), queue: resolved.queue ?? createChatTaskQueue(),
        getConfig: resolved.getConfig ?? (() => {
            const envelope = store.loadEnvelope?.();
            return resolveConfig({ globalConfig: runtimeAdapter.getSettings?.(), chatConfig: envelope?.ok ? envelope.value.configOverrides : envelope?.configOverrides });
        }), getPreset: resolved.getPreset ?? (id => presets.find(item => item.id === id)),
        hasProfile: resolved.hasProfile ?? (id => runtimeAdapter.listProfiles().some(profile => profile.id === id)),
        ensureMessageId, applyPatch: applyValidatedPatch, getChecks: resolved.getChecks ?? (() => []), recordDiagnostic: resolved.recordDiagnostic ?? (() => {}), prepareSwipeGeneration: resolved.prepareSwipeGeneration ?? (input => store.prepareSwipeGeneration(input)),
    });
    orchestrator.start();
    try {
        await orchestrator.initializeChat();
    } catch (error) {
        orchestrator.stop();
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
