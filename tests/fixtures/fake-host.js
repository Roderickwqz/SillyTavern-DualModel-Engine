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

// Stateful adapter used by cross-component acceptance tests.  Its workflow
// methods drive the public bootstrap composition root rather than bypassing it.
export function createAcceptanceHost({ requestPatch } = {}) {
    const listeners = new Map(); const tools = new Map(); const queue = createChatTaskQueue(); let sequence = 0; let failSaves = false;
    const recorderRequests = [];
    const settings = { enabled: true, recorderProfileId: 'recorder', rulePresetId: 'narrative', adjudication: 'automatic-tool', injectionBudget: 1200, customPresets: [] };
    const context = { chatId: 'acceptance-chat', groupId: null, chat: [], chatMetadata: {} };
    const adapter = {
        events: { CHAT_CHANGED: 'chat', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', GENERATION_STOPPED: 'stopped', MESSAGE_SWIPED: 'swiped', MESSAGE_EDITED: 'edited', MESSAGE_DELETED: 'deleted', MESSAGE_SWIPE_DELETED: 'swipe-deleted' },
        canInjectPrompt: true, canPersist: true, canRegisterTools: true, getContext: () => context, getSettings: () => settings, getGlobalSettings: () => settings, getChatMetadata: () => context.chatMetadata,
        getCurrentCharacter: () => context.character, listProfiles: () => [{ id: 'recorder', name: 'Deterministic Recorder' }],
        on: (event, listener) => { const set = listeners.get(event) ?? new Set(); set.add(listener); listeners.set(event, set); },
        off: (event, listener) => { const set = listeners.get(event); set?.delete(listener); if (set?.size === 0) listeners.delete(event); },
        registerTool: definition => tools.set(definition.name, definition), unregisterTool: name => tools.delete(name),
        saveChat: async () => { if (failSaves) throw new Error('forced-save-failure'); }, saveSettings: async () => { if (failSaves) throw new Error('forced-save-failure'); },
        saveGlobalSettings: async value => { Object.assign(settings, value); if (failSaves) throw new Error('forced-save-failure'); },
        countTokens: async text => Math.ceil(text.length / 3), setPrompt: () => {}, clearPrompt: () => {}, requestProfile: async () => ({ content: JSON.stringify({ base_version: 0, operations: [] }) }),
    };
    let app;
    const id = prefix => `${prefix}-${++sequence}`;
    const appendUser = text => context.chat.push({ is_user: true, mes: text, extra: {} });
    const appendAssistant = text => { const message = { is_user: false, mes: text, extra: {}, swipe_id: 0, swipe_info: [{ extra: {} }] }; context.chat.push(message); return message; };
    const emit = async (event, ...args) => Promise.all([...listeners.get(event) ?? []].map(listener => listener(...args)));
    const wait = async chatId => { await queue.waitForIdle(chatId ?? context.chatId); await Promise.resolve(); };
    return {
        adapter,
        dependencies: {
            queue, makeId: () => id('id'), nextUint32: () => 19,
            modelService: { requestPatch: async input => { recorderRequests.push(structuredClone(input)); return (requestPatch ?? (async value => ({ patch: { base_version: value.baseVersion, operations: value.presetId === 'd20-lite' ? [] : value.presetId === 'relationship-meter' ? [{ op: 'replace', path: '/relationship/trust', value: 1, reason: 'deterministic acceptance update' }] : [{ op: 'add', path: '/inventory/-', value: `recorded-${value.baseVersion}`, reason: 'deterministic acceptance update' }] } })))(input); }, requestSummary: async input => ({ state: input.oldState }), requestDecision: async () => ({ decision: 'none' }) },
            confirmRecalculation: async () => true,
        },
        attach(value) { app = value; return value; },
        reloadAdapter() { return adapter; },
        currentPresetId: () => context.chatMetadata.dualModelEngine?.preset?.id,
        currentState: () => structuredClone(context.chatMetadata.dualModelEngine?.activeSnapshot),
        snapshotPluginData: () => JSON.stringify({ metadata: context.chatMetadata.dualModelEngine ?? null, branches: context.chat.flatMap(message => (message.swipe_info ?? []).map(swipe => swipe.extra?.dualModelEngine?.branch).filter(Boolean)), settings: { customPresets: settings.customPresets } }),
        failAllSaves(value = true) { failSaves = value; },
        setGroup(value = 'group') { context.groupId = value; }, setChat(idValue) { context.chatId = idValue; },
        seedChat(messages) { context.chat.splice(0, context.chat.length, ...structuredClone(messages)); },
        async runNarrativeTurn(player, assistant) { appendUser(player); const started = await app.orchestrator.beforeGeneration('normal'); if (!started?.ok) return started; appendAssistant(assistant); const ended = await app.orchestrator.afterGeneration(); await wait(); return ended?.queued ? { ok: context.chatMetadata.dualModelEngine?.stateVersion > 0, started, ended, diagnostics: settings.diagnostics } : ended; },
        async runContinue(delta) { const message = context.chat.findLast(item => !item.is_user); const started = await app.orchestrator.beforeGeneration('continue'); message.mes += delta; const ended = await app.orchestrator.afterGeneration(); await wait(); return ended?.queued ? { ok: true, started, ended } : ended; },
        async bindD20() { settings.rulePresetId = 'd20-lite'; const bound = await app.presetManager.bindChat(context.chatMetadata, 'd20-lite', { confirmedReset: true }); await app.orchestrator.initializeChat(); const actor = { abilities: { strength: 10, dexterity: 14, constitution: 10, intelligence: 10, wisdom: 10, charisma: 10 }, proficientSkills: ['sleight_of_hand'], proficiencyBonus: 2, hp: { current: 10, max: 10, temporary: 0 }, conditions: [] }; const envelope = context.chatMetadata.dualModelEngine; envelope.initialSnapshot.actors.player = structuredClone(actor); envelope.activeSnapshot.actors.player = structuredClone(actor); await app.orchestrator.initializeChat(); return bound; },
        async runFormalCheck(request) { const chatId = context.chatId; appendUser(request.action); await app.orchestrator.beforeGeneration('normal'); const staged = await tools.get('DualModelResolveD20Check').action(request); appendAssistant('The lock opens.'); const ended = await app.orchestrator.afterGeneration(); await wait(chatId); return { record: app.ledger.list().find(record => record.checkId === staged.checkId), staged, ended, records: app.ledger.list(), diagnostics: settings.diagnostics, envelope: structuredClone(context.chatMetadata.dualModelEngine) }; },
        async createSwipeAndRunSameCheck(request) { const chatId = context.chatId; const message = context.chat.findLast(item => !item.is_user); message.swipe_id = 1; message.mes = 'A different outcome.'; message.swipe_info.push({ extra: {} }); await emit('swiped', context.chat.length - 1); await app.orchestrator.beforeGeneration('swipe'); const staged = await tools.get('DualModelResolveD20Check').action(request); await app.orchestrator.afterGeneration(); await wait(chatId); return { record: app.ledger.list().find(record => record.checkId === staged.checkId), staged, records: app.ledger.list(), envelope: structuredClone(context.chatMetadata.dualModelEngine) }; },
        async importAndBindPreset(text) { await app.presetManager.importPreset(text); return app.presetManager.bindChat(context.chatMetadata, 'relationship-meter', { confirmedReset: true }); },
        async explicitReroll() {
            let settingsHost = document.querySelector('#extensions_settings');
            if (!settingsHost) { settingsHost = document.createElement('div'); settingsHost.id = 'extensions_settings'; document.body.append(settingsHost); }
            await app.ui.mount(); await app.ui.render();
            const selected = settingsHost.querySelector('[data-dme-check-id]');
            selected?.click(); settingsHost.querySelector('[data-dme-action="reroll"]')?.click();
            await wait(); await Promise.resolve();
            return { record: app.ledger.list().at(-1) };
        },
        async restoreSelectedSwipe() { const index = context.chat.length - 1; const value = await emit('swiped', index); await wait(); return value.at(-1); },
        async deleteLastMessage() { const chatId = context.chatId; context.chat.pop(); const value = await emit('deleted'); await wait(chatId); return value.at(-1); },
        async editLastMessage(text) { const chatId = context.chatId; const index = context.chat.length - 1; context.chat[index].mes = text; const value = await emit('edited', index); await wait(chatId); return value.at(-1); },
        async switchChat(idValue) { const old = context.chatId; context.chatId = idValue; await emit('chat'); await wait(old); },
        app: () => app,
        records: () => app.ledger.list(),
        recorderRequests: () => structuredClone(recorderRequests),
    };
}
