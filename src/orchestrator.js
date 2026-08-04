function envelopeValue(value) { return value?.ok === true ? value.value : value; }
function clone(value) { return structuredClone(value); }
const conflicts = new Set(['stale-chat', 'stale-message', 'stale-swipe', 'branch-conflict', 'head-conflict', 'state-conflict', 'duplicate-request']);

export function createOrchestrator(deps) {
    const supported = new Set(['normal', 'swipe', 'regenerate', 'continue']);
    let activeChatId = null; let generation = null; let started = false; const unbind = [];
    const diagnostic = value => { try { return Promise.resolve(deps.recordDiagnostic?.(value)).catch(() => undefined); } catch { return undefined; } };
    function context() { return deps.adapter.getContext(); }
    function messageId(message) { return deps.ensureMessageId ? deps.ensureMessageId(message) : (message.extra?.dualModelEngine?.messageId); }
    async function initializeChat() {
        const current = context(); activeChatId = current.chatId; const config = deps.getConfig();
        if (current.groupId || !config.enabled) { try { deps.promptInjector.clear(); } catch (error) { diagnostic({ reason: 'prompt-clear-failed', error }); } return { enabled: false, reason: current.groupId ? 'group-chat' : 'disabled' }; }
        try { await deps.rollbackManager?.repairOrphanedHead?.(); } catch (error) { diagnostic({ reason: 'orphan-repair-failed', error }); }
        const loaded = deps.store.loadEnvelope(); let envelope = envelopeValue(loaded);
        const configuredPreset = deps.getPreset(config.rulePresetId);
        if (!envelope || loaded?.ok === false) {
            const created = await deps.store.ensureEnvelope?.({ presetId: config.rulePresetId, initialState: configuredPreset?.initialState });
            if (!created?.ok) { diagnostic({ reason: 'missing-envelope', result: created }); try { deps.promptInjector.clear(); } catch (error) { diagnostic({ reason: 'prompt-clear-failed', error }); } return { enabled: false, reason: 'missing-envelope' }; }
            envelope = envelopeValue(created);
        }
        const preset = deps.getPreset(envelope.preset.id);
        try { await deps.promptInjector.refresh({ state: envelope.activeSnapshot, budgetTokens: config.injectionBudget, injection: preset.injection }); } catch (error) { diagnostic({ reason: 'prompt-refresh-failed', error }); }
        return { enabled: true };
    }
    function capture(type, current, envelope, config, preset) {
        const target = ['swipe', 'continue', 'regenerate'].includes(type) ? current.chat.findLast(isFinalAssistant) : null;
        const targetId = target ? messageId(target) : null;
        const previousUser = current.chat.findLast(m => m.is_user);
        const existing = deps.store.getBranch?.(target, target?.swipe_id ?? 0);
        const prepared = ['swipe', 'regenerate'].includes(type) ? (deps.rollbackManager?.prepareSwipeGeneration?.(current.chat.length - 1, type) ?? deps.prepareSwipeGeneration?.({ type, target, envelope })) : null;
        if (prepared?.ok === false) return null;
        const branch = type === 'continue' ? existing?.branchId : null;
        return { type, chatId: current.chatId, expectedHeadRevision: prepared?.expectedHeadRevision ?? envelope.headRevision, baseVersion: prepared?.baseStateVersion ?? envelope.stateVersion, baseSnapshot: clone(prepared?.baseSnapshot ?? envelope.activeSnapshot), baseBranchId: prepared?.baseBranchId ?? (['swipe', 'regenerate'].includes(type) ? existing?.branchId ?? null : null), reusableChecks: clone(prepared?.reusableChecks ?? []), effectiveConfig: clone(config), preset, requestId: deps.makeId?.() ?? crypto.randomUUID(), branchId: branch ?? (deps.makeId?.() ?? crypto.randomUUID()), targetMessageId: targetId, assistantText: type === 'continue' ? (target?.mes ?? '') : null, playerText: previousUser?.mes ?? '', userMessageId: previousUser ? messageId(previousUser) : null };
    }
    async function beforeGeneration(type) {
        if (!supported.has(type)) return { ignored: true, reason: 'unsupported-generation-type' };
        if (generation) return { ignored: true, reason: 'tool-recursion' };
        await deps.queue.waitForIdle(activeChatId ?? context().chatId);
        const current = context(); const loaded = deps.store.loadEnvelope(); const envelope = envelopeValue(loaded); const config = clone(deps.getConfig());
        if (!envelope || loaded?.ok === false) { diagnostic({ reason: 'missing-envelope' }); return { ignored: true, reason: 'missing-envelope' }; }
        if (current.groupId || !config.enabled) { deps.promptInjector.clear(); return { ignored: true, reason: current.groupId ? 'group-chat' : 'disabled' }; }
        if (!deps.hasProfile(config.recorderProfileId)) { diagnostic({ reason: 'missing-recorder-profile', profileId: config.recorderProfileId }); return { ignored: true, reason: 'missing-recorder-profile' }; }
        const preset = deps.getPreset(config.rulePresetId);
        activeChatId = current.chatId; generation = capture(type, current, envelope, config, preset); if (!generation) { diagnostic({ reason: 'missing-source-branch' }); return { ignored: true, reason: 'missing-source-branch' }; }
        const hardRuleText = deps.formatReusableChecks?.(generation.reusableChecks) ?? '';
        try { await deps.promptInjector.refresh({ state: envelope.activeSnapshot, budgetTokens: config.injectionBudget, injection: preset.injection, hardRuleText }); } catch (error) { generation = null; diagnostic({ reason: 'prompt-refresh-failed', error }); return { ignored: true, reason: 'prompt-refresh-failed' }; }
        return { ok: true, requestId: generation.requestId };
    }
    function isFinalAssistant(message) { return !message?.is_user && !message?.is_system && !message?.extra?.tool_invocations && !message?.extra?.tool_call_id && !message?.extra?.tool_calls && !message?.tool_calls; }
    function locate(current, captured) {
        if (current.chatId !== captured.chatId) return { ok: false, reason: 'stale-chat' };
        const index = ['swipe', 'continue'].includes(captured.type) ? current.chat.findIndex(m => m.extra?.dualModelEngine?.messageId === captured.targetMessageId && isFinalAssistant(m)) : current.chat.findLastIndex(isFinalAssistant);
        return index < 0 ? { ok: false, reason: 'missing-final-message' } : { ok: true, message: current.chat[index], messageIndex: index };
    }
    async function process(captured, _message, signal) {
        const now = context(); if (now.chatId !== captured.chatId) return { ok: false, reason: 'stale-chat' };
        const message = now.chat.find(item => item?.extra?.dualModelEngine?.messageId === captured.assistantMessageId);
        if (!message || !isFinalAssistant(message) || (message.swipe_id ?? 0) !== captured.swipeId) return { ok: false, reason: 'stale-message' };
        const assistantText = captured.type === 'continue' ? message.mes.slice(captured.assistantText.length) : message.mes;
        if (captured.type === 'continue' && (!message.mes.startsWith(captured.assistantText) || message.mes.length < captured.assistantText.length)) return { ok: false, reason: 'stale-message' };
        const checks = clone(captured.checks); const response = await deps.modelService.requestPatch({ profileId: captured.effectiveConfig.recorderProfileId, baseVersion: captured.baseVersion, oldState: captured.baseSnapshot, playerText: captured.playerText, assistantText, checks, signal });
        const validation = deps.validator.validatePatch(captured.effectiveConfig.rulePresetId, response.patch, { expectedVersion: captured.baseVersion, allowedPaths: captured.preset.allowedPaths, lockedPaths: [...captured.preset.lockedPaths, ...(captured.preset.ruleLockedPaths ?? [])] });
        if (!validation.ok) throw new Error(JSON.stringify(validation.errors));
        const applied = deps.applyPatch({ state: captured.baseSnapshot, patch: response.patch, policy: captured.preset, validateState: state => deps.validator.validateState(captured.effectiveConfig.rulePresetId, state) });
        if (!applied.ok) throw new Error(JSON.stringify(applied.errors)); applied.value.version = captured.baseVersion + 1;
        return deps.store.commitSegment({ chatId: captured.chatId, message, messageId: captured.assistantMessageId, branchId: captured.branchId, swipeId: captured.swipeId, expectedHeadRevision: captured.expectedHeadRevision, baseStateVersion: captured.baseVersion, baseSnapshot: captured.baseSnapshot, requestId: captured.requestId, userMessageId: captured.userMessageId, patch: response.patch, checks: clone(captured.checks), assistantText, nextState: applied.value, isContinue: captured.type === 'continue' });
    }
    async function replayTurn({ messageIndex, swipeId, baseSnapshot, signal }) {
        const current = context(); const config = clone(deps.getConfig());
        if (current.groupId || !config.enabled) return { ok: false, reason: 'read-only' };
        if (!deps.hasProfile(config.recorderProfileId)) return { ok: false, reason: 'missing-recorder-profile' };
        const preset = deps.getPreset(config.rulePresetId); if (!preset) return { ok: false, reason: 'missing-preset' };
        const message = current.chat[messageIndex];
        if (!message || message.is_user || message.is_system || (message.swipe_id ?? 0) !== swipeId) return { ok: false, reason: 'stale-message' };
        const branch = deps.store.getBranch(message, swipeId); if (!branch?.branchId) return { ok: false, reason: 'missing-source-branch' };
        const envelope = envelopeValue(deps.store.loadEnvelope()); if (!envelope) return { ok: false, reason: 'missing-envelope' };
        const previousUser = current.chat.slice(0, messageIndex).findLast(item => item?.is_user);
        const capturedMessageId = messageId(message); const capturedText = message.mes ?? ''; const capturedUserId = previousUser ? messageId(previousUser) : null; const capturedUserText = previousUser?.mes ?? '';
        const checks = clone(branch.segments?.flatMap(segment => segment.checks ?? []) ?? []);
        try {
            const response = await deps.modelService.requestPatch({ profileId: config.recorderProfileId, baseVersion: baseSnapshot.version, oldState: baseSnapshot, playerText: capturedUserText, assistantText: capturedText, checks, signal });
            const latest = context(); const latestMessage = latest.chat?.find(item => item?.extra?.dualModelEngine?.messageId === capturedMessageId); const latestUser = capturedUserId ? latest.chat?.find(item => item?.extra?.dualModelEngine?.messageId === capturedUserId) : null;
            if (latest.chatId !== current.chatId || !latestMessage || latestMessage.mes !== capturedText || (latestMessage.swipe_id ?? 0) !== swipeId || (capturedUserId && (!latestUser || latestUser.mes !== capturedUserText))) return { ok: false, reason: 'assistant-text-mismatch' };
            const validation = deps.validator.validatePatch(config.rulePresetId, response.patch, { expectedVersion: baseSnapshot.version, allowedPaths: preset.allowedPaths, lockedPaths: [...preset.lockedPaths, ...(preset.ruleLockedPaths ?? [])] });
            if (!validation.ok) return { ok: false, reason: 'invalid-patch' };
            const applied = deps.applyPatch({ state: baseSnapshot, patch: response.patch, policy: preset, validateState: state => deps.validator.validateState(config.rulePresetId, state) });
            if (!applied.ok) return { ok: false, reason: 'invalid-state' }; applied.value.version = baseSnapshot.version + 1;
            const committed = await deps.store.commitSegment({ chatId: current.chatId, message: latestMessage, messageId: capturedMessageId, branchId: branch.branchId, swipeId, expectedHeadRevision: envelope.headRevision, baseStateVersion: baseSnapshot.version, baseSnapshot, requestId: deps.makeId?.() ?? crypto.randomUUID(), userMessageId: capturedUserId, patch: response.patch, checks, assistantText: capturedText, nextState: applied.value, isContinue: false });
            return committed.ok ? { ok: true, snapshot: clone(applied.value), stateVersion: applied.value.version } : committed;
        } catch (error) { return { ok: false, reason: 'replay-failed', error }; }
    }
    async function afterGeneration() {
        if (!generation) return { ignored: true, reason: 'no-matching-generation' };
        const located = locate(context(), generation); const captured = generation; generation = null;
        if (!located.ok) { await Promise.resolve(deps.rollbackManager?.abortReplacement?.()).catch(() => undefined); diagnostic({ requestId: captured.requestId, ...located }); return located; }
        captured.assistantMessageId = messageId(located.message); captured.swipeId = located.message.swipe_id ?? 0; captured.checks = []; for (const record of [...captured.reusableChecks, ...deps.getChecks(captured)]) if (record?.checkId && !captured.checks.some(existing => existing.checkId === record.checkId)) captured.checks.push(clone(record));
        let failed = false; const fail = async detail => { if (failed) return; failed = true; const outcome = await deps.store.markBranchFailed?.({ chatId: captured.chatId, messageId: captured.assistantMessageId, swipeId: captured.swipeId, branchId: captured.branchId, requestId: captured.requestId, baseSnapshot: captured.baseSnapshot, baseStateVersion: captured.baseVersion, isContinue: captured.type === 'continue', baseBranchId: captured.baseBranchId }); if (!outcome?.ok) diagnostic({ requestId: captured.requestId, ...detail, failureResult: outcome }); else diagnostic({ requestId: captured.requestId, ...detail }); };
        let replacementSettled = false; const settleReplacement = ok => { if (replacementSettled) return null; replacementSettled = true; try { if (ok) { deps.rollbackManager?.completeReplacement?.(); return null; } return deps.rollbackManager?.abortReplacement?.() ?? null; } catch (error) { diagnostic({ requestId: captured.requestId, reason: 'replacement-settlement-failed', error }); return null; } };
        let queued; try { queued = deps.queue.enqueue(captured.chatId, captured.requestId, signal => process(captured, located.message, signal)); } catch (error) { await Promise.resolve(deps.rollbackManager?.abortReplacement?.()).catch(() => undefined); await fail({ reason: 'queue-enqueue-failed', error }); return { ok: false, reason: 'queue-enqueue-failed' }; }
        void queued.then(async result => { if (result?.ok) { settleReplacement(true); try { const env = envelopeValue(deps.store.loadEnvelope()); await deps.promptInjector.refresh({ state: env.activeSnapshot, budgetTokens: captured.effectiveConfig.injectionBudget, injection: captured.preset.injection }); } catch (error) { diagnostic({ requestId: captured.requestId, reason: 'prompt-refresh-failed', error }); } } else { const abort = settleReplacement(false); if (abort) await Promise.resolve(abort).catch(error => diagnostic({ requestId: captured.requestId, reason: 'replacement-settlement-failed', error })); if (conflicts.has(result?.reason)) diagnostic({ requestId: captured.requestId, ...result }); else await fail({ reason: result?.reason ?? 'task-failed', result }); } }, async error => { const abort = settleReplacement(false); if (abort) await Promise.resolve(abort).catch(settleError => diagnostic({ requestId: captured.requestId, reason: 'replacement-settlement-failed', error: settleError })); if (error?.name === 'AbortError') diagnostic({ requestId: captured.requestId, reason: 'cancelled' }); else await fail({ reason: 'recorder-failed', error }); }).catch(error => diagnostic({ requestId: captured.requestId, reason: 'settlement-observer-failed', error }));
        return { ok: true, queued: true };
    }
    function generationStopped(reason = 'host-stopped') { const stopped = generation; generation = null; if (stopped) { diagnostic({ requestId: stopped.requestId, reason }); void Promise.resolve(deps.rollbackManager?.abortReplacement?.()).catch(() => undefined); } }
    const handlers = { chatChanged: () => { const previous = activeChatId; generationStopped('chat-changed'); if (previous) deps.queue.cancelChat(previous, 'chat-changed'); deps.rollbackManager?.refresh?.(); return initializeChat(); }, beforeGeneration: (type, _options, dryRun) => dryRun ? undefined : beforeGeneration(type), generationEnded: afterGeneration, generationStopped: () => generationStopped() };
    function cleanup() { let first; while (unbind.length) { try { unbind.pop()(); } catch (error) { first ??= error; } } try { deps.rollbackManager?.destroy?.(); } catch (error) { first ??= error; } try { generationStopped('orchestrator-stopped'); } catch (error) { first ??= error; } try { if (activeChatId) deps.queue.cancelChat(activeChatId, 'orchestrator-stopped'); } catch (error) { first ??= error; } try { deps.promptInjector.clear(); } catch (error) { first ??= error; } started = Boolean(first); return first; }
    function start() { if (started) return; started = true; try { for (const [name, fn] of [[deps.adapter.events?.CHAT_CHANGED, handlers.chatChanged], [deps.adapter.events?.GENERATION_AFTER_COMMANDS, handlers.beforeGeneration], [deps.adapter.events?.GENERATION_ENDED, handlers.generationEnded], [deps.adapter.events?.GENERATION_STOPPED, handlers.generationStopped]]) if (name) { deps.adapter.on(name, fn); unbind.push(() => deps.adapter.off(name, fn)); } } catch (error) { cleanup(); throw error; } }
    function stop() { const error = cleanup(); if (error) throw error; }
    return { start, stop, initializeChat, beforeGeneration, afterGeneration, replayTurn, getStatus: () => ({ activeChatId, generation: Boolean(generation), queue: activeChatId ? deps.queue.getStatus(activeChatId) : { state: 'idle', requestId: null } }) };
}
