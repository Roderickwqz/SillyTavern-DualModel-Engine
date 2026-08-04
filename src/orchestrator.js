function envelopeValue(value) { return value?.ok === true ? value.value : value; }
function clone(value) { return structuredClone(value); }
const conflicts = new Set(['stale-chat', 'stale-message', 'stale-swipe', 'branch-conflict', 'head-conflict', 'state-conflict', 'duplicate-request']);

export function createOrchestrator(deps) {
    const supported = new Set(['normal', 'swipe', 'regenerate', 'continue']);
    let activeChatId = null; let generation = null; let started = false; const unbind = [];
    const diagnostic = value => { try { return deps.recordDiagnostic?.(value); } catch { return undefined; } };
    function context() { return deps.adapter.getContext(); }
    function messageId(message) { return deps.ensureMessageId ? deps.ensureMessageId(message) : (message.extra?.dualModelEngine?.messageId); }
    async function initializeChat() {
        const current = context(); activeChatId = current.chatId; const config = deps.getConfig();
        if (current.groupId || !config.enabled) { deps.promptInjector.clear(); return { enabled: false, reason: current.groupId ? 'group-chat' : 'disabled' }; }
        const envelope = envelopeValue(deps.store.loadEnvelope()); const preset = deps.getPreset(envelope.preset.id);
        try { await deps.promptInjector.refresh({ state: envelope.activeSnapshot, budgetTokens: config.injectionBudget, injection: preset.injection }); } catch (error) { diagnostic({ reason: 'prompt-refresh-failed', error }); }
        return { enabled: true };
    }
    function capture(type, current, envelope, config, preset) {
        const target = ['swipe', 'continue', 'regenerate'].includes(type) ? current.chat.findLast(m => !m.is_user && !m.is_system) : null;
        const targetId = target ? messageId(target) : null;
        const previousUser = current.chat.findLast(m => m.is_user);
        const branch = type === 'continue' ? deps.store.getBranch?.(target, target?.swipe_id ?? 0)?.branchId : null;
        return { type, chatId: current.chatId, expectedHeadRevision: envelope.headRevision, baseVersion: envelope.stateVersion, baseSnapshot: clone(envelope.activeSnapshot), effectiveConfig: clone(config), preset, requestId: deps.makeId?.() ?? crypto.randomUUID(), branchId: branch ?? (deps.makeId?.() ?? crypto.randomUUID()), targetMessageId: targetId, assistantTextLength: type === 'continue' ? (target?.mes?.length ?? 0) : 0, playerText: previousUser?.mes ?? '', userMessageId: previousUser ? messageId(previousUser) : null };
    }
    async function beforeGeneration(type) {
        if (!supported.has(type)) return { ignored: true, reason: 'unsupported-generation-type' };
        if (generation) return { ignored: true, reason: 'tool-recursion' };
        await deps.queue.waitForIdle(activeChatId ?? context().chatId);
        const current = context(); const envelope = envelopeValue(deps.store.loadEnvelope()); const config = clone(deps.getConfig());
        if (current.groupId || !config.enabled) { deps.promptInjector.clear(); return { ignored: true, reason: current.groupId ? 'group-chat' : 'disabled' }; }
        if (!deps.hasProfile(config.recorderProfileId)) { diagnostic({ reason: 'missing-recorder-profile', profileId: config.recorderProfileId }); return { ignored: true, reason: 'missing-recorder-profile' }; }
        const preset = deps.getPreset(config.rulePresetId);
        try { await deps.promptInjector.refresh({ state: envelope.activeSnapshot, budgetTokens: config.injectionBudget, injection: preset.injection }); } catch (error) { diagnostic({ reason: 'prompt-refresh-failed', error }); return { ignored: true, reason: 'prompt-refresh-failed' }; }
        activeChatId = current.chatId; generation = capture(type, current, envelope, config, preset); return { ok: true, requestId: generation.requestId };
    }
    function locate(current, captured) {
        if (current.chatId !== captured.chatId) return { ok: false, reason: 'stale-chat' };
        const index = ['swipe', 'continue'].includes(captured.type) ? current.chat.findIndex(m => m.extra?.dualModelEngine?.messageId === captured.targetMessageId) : current.chat.findLastIndex(m => !m.is_user && !m.is_system && !m.extra?.tool_invocations && !m.extra?.tool_call_id);
        return index < 0 ? { ok: false, reason: 'missing-final-message' } : { ok: true, message: current.chat[index], messageIndex: index };
    }
    async function process(captured, message, signal) {
        const now = context(); if (now.chatId !== captured.chatId) return { ok: false, reason: 'stale-chat' };
        if (!now.chat.includes(message) || message.extra?.dualModelEngine?.messageId !== captured.assistantMessageId) return { ok: false, reason: 'stale-message' };
        const assistantText = captured.type === 'continue' ? message.mes.slice(captured.assistantTextLength) : message.mes;
        const checks = deps.getChecks(captured); const response = await deps.modelService.requestPatch({ profileId: captured.effectiveConfig.recorderProfileId, baseVersion: captured.baseVersion, oldState: captured.baseSnapshot, playerText: captured.playerText, assistantText, checks, signal });
        const validation = deps.validator.validatePatch(captured.effectiveConfig.rulePresetId, response.patch, { expectedVersion: captured.baseVersion, allowedPaths: captured.preset.allowedPaths, lockedPaths: [...captured.preset.lockedPaths, ...(captured.preset.ruleLockedPaths ?? [])] });
        if (!validation.ok) throw new Error(JSON.stringify(validation.errors));
        const applied = deps.applyPatch({ state: captured.baseSnapshot, patch: response.patch, policy: captured.preset, validateState: state => deps.validator.validateState(captured.effectiveConfig.rulePresetId, state) });
        if (!applied.ok) throw new Error(JSON.stringify(applied.errors)); applied.value.version = captured.baseVersion + 1;
        return deps.store.commitSegment({ chatId: captured.chatId, message, messageId: captured.assistantMessageId, branchId: captured.branchId, swipeId: captured.swipeId, expectedHeadRevision: captured.expectedHeadRevision, baseStateVersion: captured.baseVersion, baseSnapshot: captured.baseSnapshot, requestId: captured.requestId, userMessageId: captured.userMessageId, patch: response.patch, checks, assistantText, nextState: applied.value, isContinue: captured.type === 'continue' });
    }
    async function afterGeneration() {
        if (!generation) return { ignored: true, reason: 'no-matching-generation' };
        const located = locate(context(), generation); const captured = generation; generation = null;
        if (!located.ok) { diagnostic({ requestId: captured.requestId, ...located }); return located; }
        captured.assistantMessageId = messageId(located.message); captured.swipeId = located.message.swipe_id ?? 0;
        let queued; try { queued = deps.queue.enqueue(captured.chatId, captured.requestId, signal => process(captured, located.message, signal)); } catch (error) { diagnostic({ requestId: captured.requestId, reason: 'queue-enqueue-failed', error }); return { ok: false, reason: 'queue-enqueue-failed' }; }
        void queued.then(async result => { if (result?.ok) { try { const env = envelopeValue(deps.store.loadEnvelope()); await deps.promptInjector.refresh({ state: env.activeSnapshot, budgetTokens: captured.effectiveConfig.injectionBudget, injection: captured.preset.injection }); } catch (error) { diagnostic({ requestId: captured.requestId, reason: 'prompt-refresh-failed', error }); } } else if (conflicts.has(result?.reason)) diagnostic({ requestId: captured.requestId, ...result }); else deps.handleTaskFailure?.({ message: located.message, swipeId: captured.swipeId, requestId: captured.requestId, result }); }).catch(error => { if (error?.name === 'AbortError') return diagnostic({ requestId: captured.requestId, reason: 'cancelled' }); deps.handleTaskFailure?.({ message: located.message, swipeId: captured.swipeId, requestId: captured.requestId, error }); });
        return { ok: true, queued: true };
    }
    function generationStopped(reason = 'host-stopped') { if (!generation) return; diagnostic({ requestId: generation.requestId, reason }); generation = null; }
    const handlers = { chatChanged: () => { const previous = activeChatId; generationStopped('chat-changed'); if (previous) deps.queue.cancelChat(previous, 'chat-changed'); return initializeChat(); }, beforeGeneration: (type, _options, dryRun) => dryRun ? undefined : beforeGeneration(type), generationEnded: afterGeneration, generationStopped: () => generationStopped() };
    function start() { if (started) return; started = true; for (const [name, fn] of [[deps.adapter.events?.CHAT_CHANGED, handlers.chatChanged], [deps.adapter.events?.GENERATION_AFTER_COMMANDS, handlers.beforeGeneration], [deps.adapter.events?.GENERATION_ENDED, handlers.generationEnded], [deps.adapter.events?.GENERATION_STOPPED, handlers.generationStopped]]) if (name) { deps.adapter.on(name, fn); unbind.push(() => deps.adapter.off(name, fn)); } }
    function stop() { while (unbind.length) unbind.pop()(); started = false; generationStopped('orchestrator-stopped'); if (activeChatId) deps.queue.cancelChat(activeChatId, 'orchestrator-stopped'); deps.promptInjector.clear(); }
    return { start, stop, initializeChat, beforeGeneration, afterGeneration, getStatus: () => ({ activeChatId, generation: Boolean(generation), queue: activeChatId ? deps.queue.getStatus(activeChatId) : { state: 'idle', requestId: null } }) };
}
