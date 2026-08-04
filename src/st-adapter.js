export function createSTAdapter(host) {
    return {
        events: host.eventTypes ?? {},
        getContext: () => host.getContext(),
        on: (eventName, handler) => host.eventSource?.on(eventName, handler),
        off: (eventName, handler) => host.eventSource?.removeListener(eventName, handler),
        setPrompt: (key, value, options) => host.setExtensionPrompt?.(key, value, options.position, options.depth, false, options.role),
        clearPrompt: (key, options) => host.setExtensionPrompt?.(key, '', options.position, options.depth, false, options.role),
        listProfiles: () => host.getProfiles?.() ?? [],
        requestProfile: (profileId, messages, maxTokens, options, overridePayload = {}) =>
            host.sendRequest(profileId, messages, maxTokens, options, overridePayload),
        registerTool: typeof host.registerTool === 'function' ? definition => host.registerTool(definition) : undefined,
        unregisterTool: typeof host.unregisterTool === 'function' ? name => host.unregisterTool(name) : undefined,
        probeMainTool: typeof host.probeMainTool === 'function' ? params => host.probeMainTool(params) : undefined,
        getMainApiModelLabel: typeof host.getMainApiModelLabel === 'function' ? () => host.getMainApiModelLabel() : () => null,
        saveChat: () => host.getContext().saveMetadata(),
        saveSettings: () => host.saveSettingsDebounced?.(),
        getSettings: () => host.getSettings?.() ?? {},
        listPresetReferences: typeof host.listPresetReferences === 'function' ? id => host.listPresetReferences(id) : undefined,
        countTokens: text => host.countTokens(text),
        canInjectPrompt: typeof host.setExtensionPrompt === 'function',
        canPersist: typeof host.getContext?.().saveMetadata === 'function',
        canRegisterTools: typeof host.registerTool === 'function',
        canProbeMainTools: typeof host.probeMainTool === 'function',
    };
}
