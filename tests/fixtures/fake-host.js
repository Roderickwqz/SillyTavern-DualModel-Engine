import { vi } from 'vitest';
import { createChatTaskQueue } from '../../src/task-queue.js';
import { createOrchestrator } from '../../src/orchestrator.js';

const preset = Object.freeze({ id: 'narrative', allowedPaths: ['/inventory'], lockedPaths: ['/version'], injection: [] });
const responsePatch = { base_version: 0, operations: [{ op: 'add', path: '/inventory/-', value: 'recorded' }] };
export function createOrchestratorTestDependencies({ chat = [], store, commit } = {}) {
    const context = { chatId: 'chat-a', groupId: null, chat };
    const envelope = { stateVersion: 0, headRevision: 0, preset: { id: 'narrative' }, activeSnapshot: { version: 0, inventory: [] } };
    let id = 0;
    return {
        adapter: { events: {}, getContext: () => context, on: vi.fn(), off: vi.fn() }, queue: createChatTaskQueue(),
        store: store ?? { loadEnvelope: () => envelope, commitSegment: vi.fn(async input => commit ?? { ok: true, stateVersion: input.baseStateVersion + 1 }) },
        getConfig: () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'narrative', injectionBudget: 1000 }), getPreset: () => preset, hasProfile: () => true,
        ensureMessageId: message => (message.extra.dualModelEngine ??= {}).messageId ??= `m${++id}`, makeId: () => `r${++id}`,
        promptInjector: { refresh: vi.fn(async () => {}), clear: vi.fn() }, modelService: { requestPatch: vi.fn(async () => ({ patch: responsePatch })) },
        validator: { validatePatch: () => ({ ok: true, errors: [] }), validateState: () => ({ ok: true, errors: [] }) },
        applyPatch: ({ state }) => ({ ok: true, value: structuredClone(state), errors: [] }), getChecks: () => [], recordDiagnostic: vi.fn(), handleTaskFailure: vi.fn(), context,
    };
}
export function createNarrativeHost(options = {}) {
    const user = { is_user: true, mes: 'user', extra: { dualModelEngine: { messageId: 'u1' } } };
    const deps = createOrchestratorTestDependencies({ chat: [user], commit: options.commit });
    const diagnostics = []; const failures = [];
    deps.recordDiagnostic = value => { diagnostics.push(value); return value; };
    deps.handleTaskFailure = value => { failures.push(value); return value; };
    const orchestrator = createOrchestrator(deps);
    const addFinalAssistant = (mes = options.assistantText ?? '') => { const m = { is_user: false, mes, extra: {}, swipe_id: 0, swipe_info: [{ extra: {} }] }; deps.context.chat.push(m); return m; };
    if (options.assistantText) addFinalAssistant(options.assistantText);
    return {
        orchestrator, diagnostics, failures, startTurn: () => orchestrator.beforeGeneration('normal'), startContinue: () => orchestrator.beforeGeneration('continue'),
        emitRecursiveBeforeGeneration: () => orchestrator.beforeGeneration('normal'), emitBefore: (type, dry) => dry ? undefined : orchestrator.beforeGeneration(type),
        addFinalAssistant, addToolIntermediary: () => deps.context.chat.push({ is_user: false, mes: 'tool', extra: { tool_invocations: [{}] }, swipe_info: [{ extra: {} }] }),
        setAssistantText: value => { deps.context.chat.at(-1).mes = value; }, switchChat: id => { deps.context.chatId = id; },
        finishGeneration: async () => { const value = await orchestrator.afterGeneration(); await deps.queue.waitForIdle('chat-a'); return value; }, finishContinue: async () => { await orchestrator.afterGeneration(); await deps.queue.waitForIdle('chat-a'); },
        recorderRequests: () => deps.modelService.requestPatch.mock.calls, lastRecorderInput: () => deps.modelService.requestPatch.mock.calls.at(-1)?.[0],
    };
}
