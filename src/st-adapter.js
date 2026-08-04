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
        registerTool: definition => host.registerTool?.(definition),
        unregisterTool: name => host.unregisterTool?.(name),
        saveChat: () => host.getContext().saveMetadata(),
        saveSettings: () => host.saveSettingsDebounced?.(),
        getSettings: () => host.getSettings?.() ?? {},
        countTokens: text => host.countTokens(text),
        canInjectPrompt: typeof host.setExtensionPrompt === 'function',
        canPersist: typeof host.getContext?.().saveMetadata === 'function',
        canRegisterTools: typeof host.registerTool === 'function',
    };
}
