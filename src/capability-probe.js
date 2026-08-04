export function probeHostCapabilities(adapter) {
    const context = adapter.getContext();
    const reasons = [];
    const isGroupChat = Boolean(context.groupId);

    if (isGroupChat) reasons.push('Group chats are not supported');
    if (!adapter.canInjectPrompt) reasons.push('Prompt injection API is unavailable');
    if (!adapter.canPersist) reasons.push('Chat metadata persistence is unavailable');

    let profiles = [];
    try {
        profiles = adapter.listProfiles();
        if (!profiles.length) reasons.push('No supported Recorder connection profile is configured');
    } catch (error) {
        reasons.push(`Connection Manager is unavailable: ${error.message}`);
    }

    return {
        supported: !isGroupChat && adapter.canInjectPrompt && adapter.canPersist,
        isGroupChat,
        profiles,
        toolApiAvailable: adapter.canRegisterTools,
        toolProbeAvailable: typeof adapter.probeMainTool === 'function',
        promptInjectionAvailable: adapter.canInjectPrompt,
        persistenceAvailable: adapter.canPersist,
        reasons,
    };
}

export async function runDynamicToolProbe(adapter) {
    const name = 'DualModelCapabilityProbe'; let invoked = false;
    const definition = { name, displayName: 'DualModel Capability Probe', description: 'Call this probe exactly once.', parameters: { type: 'object', properties: {}, additionalProperties: false }, action: async () => { invoked = true; return { ok: true }; }, shouldRegister: () => true, stealth: true };
    const label = adapter?.getMainApiModelLabel?.() ?? null;
    const persist = async report => { const safe = { supported: Boolean(report.supported), reason: report.reason ?? null, checkedAt: new Date().toISOString(), apiModelLabel: label }; try { const settings = adapter?.getSettings?.(); if (settings && typeof settings === 'object') { settings.toolProbe = safe; await adapter.saveSettings?.(); } } catch (error) { return { ...report, supported: false, reason: `Probe persistence failed: ${error.message}` }; } return { ...report, ...safe }; };
    if (typeof adapter?.registerTool !== 'function' || typeof adapter?.unregisterTool !== 'function' || typeof adapter?.probeMainTool !== 'function') return persist({ supported: false, reason: 'Tool probe API is unavailable' });
    let registered = false;
    try {
        registered = true; adapter.registerTool(definition);
        const result = await adapter.probeMainTool({ prompt: `Call ${name} exactly once.`, definition, responseLength: 32 });
        const errors = result?.invocation?.errors ?? [];
        return await persist({ supported: Boolean(result?.supported && invoked && !errors.length), reason: result?.reason ?? (invoked && !errors.length ? null : 'Model response did not successfully invoke the probe tool'), invocation: result?.invocation });
    } catch (error) { return persist({ supported: false, reason: error?.message ?? String(error) }); }
    finally { if (registered) try { adapter.unregisterTool(name); } catch { /* cleanup must not falsify the probe result */ } }
}
