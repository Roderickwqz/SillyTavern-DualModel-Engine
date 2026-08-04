export function createSTAdapter(host) {
    const namespace = 'dualModelEngine';
    const getCharacter = () => {
        const context = host.getContext?.();
        return context?.characters?.[context?.characterId] ?? context?.character ?? null;
    };
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
        getGlobalSettings: () => host.getSettings?.() ?? {},
        async saveGlobalSettings(value) {
            const settings = host.getSettings?.(); if (!settings || typeof settings !== 'object') throw new Error('Global extension settings are unavailable');
            const before = structuredClone(settings); for (const key of Object.keys(settings)) delete settings[key]; Object.assign(settings, structuredClone(value));
            try { await host.saveSettingsDebounced?.(); } catch (error) { for (const key of Object.keys(settings)) delete settings[key]; Object.assign(settings, before); throw error; }
        },
        getCurrentCharacter: getCharacter,
        async saveCurrentCharacter(value) {
            const character = getCharacter(); if (!character) throw new Error('Current character is unavailable');
            character.data ??= {}; character.data.extensions ??= {}; const had = Object.hasOwn(character.data.extensions, namespace); const before = structuredClone(character.data.extensions[namespace]); character.data.extensions[namespace] = structuredClone(value);
            try {
                const context = host.getContext?.();
                if (typeof context?.writeExtensionField !== 'function') throw new Error('Character extension persistence is unavailable');
                await context.writeExtensionField(context.characterId, namespace, structuredClone(value));
            } catch (error) { if (had) character.data.extensions[namespace] = before; else delete character.data.extensions[namespace]; throw error; }
        },
        getChatMetadata: () => host.getContext?.()?.chatMetadata ?? null,
        async saveChatSettings(value) {
            const context = host.getContext?.(); const metadata = context?.chatMetadata; if (!metadata) throw new Error('Chat metadata is unavailable');
            const had = Object.hasOwn(metadata, namespace); const before = structuredClone(metadata[namespace]); metadata[namespace] ??= {}; metadata[namespace].configOverrides = structuredClone(value);
            try { await context.saveMetadata?.(); } catch (error) { if (had) metadata[namespace] = before; else delete metadata[namespace]; throw error; }
        },
        listPresetReferences: typeof host.listPresetReferences === 'function' ? id => host.listPresetReferences(id) : undefined,
        countTokens: text => host.countTokens(text),
        canInjectPrompt: typeof host.setExtensionPrompt === 'function',
        canPersist: typeof host.getContext?.().saveMetadata === 'function',
        canRegisterTools: typeof host.registerTool === 'function',
        canProbeMainTools: typeof host.probeMainTool === 'function',
    };
}
