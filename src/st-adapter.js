async function unavailableStrictPersistence() {
    throw new Error('Strict chat persistence is unavailable');
}

export function createStrictChatSaver({ getContext, getRequestHeaders, compressRequest, fetch: fetchImpl = globalThis.fetch }) {
    return async function saveStrictCharacterChat() {
        const context = getContext?.();
        if (context?.groupId) throw new Error('Strict chat persistence does not support group chat saves');
        const character = context?.characters?.[context?.characterId];
        if (!character || !context?.chatId || !Array.isArray(context?.chat) || !context?.chatMetadata) throw new Error('Strict chat persistence is unavailable');
        if (typeof getRequestHeaders !== 'function' || typeof compressRequest !== 'function' || typeof fetchImpl !== 'function') throw new Error('Strict chat persistence is unavailable');
        const request = await compressRequest({
            method: 'POST',
            cache: 'no-cache',
            headers: getRequestHeaders(),
            body: JSON.stringify({
                ch_name: character.name,
                file_name: context.chatId,
                chat: [{ chat_metadata: context.chatMetadata, user_name: 'unused', character_name: 'unused' }, ...context.chat],
                avatar_url: character.avatar,
                force: false,
            }),
        });
        const response = await fetchImpl('/api/chats/save', request);
        if (!response?.ok) throw new Error(response?.statusText || 'Strict chat persistence failed');
    };
}

export function createSTAdapter(host) {
    const namespace = 'dualModelEngine';
    const saveStrictChat = typeof host.strictSaveChat === 'function' ? () => host.strictSaveChat() : unavailableStrictPersistence;
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
        saveChat: saveStrictChat,
        saveSettings: () => host.saveSettingsDebounced?.(),
        getSettings: () => host.getSettings?.() ?? {},
        getGlobalSettings: () => host.getSettings?.() ?? {},
        async saveGlobalSettings(value) {
            const settings = host.getSettings?.(); if (!settings || typeof settings !== 'object') throw new Error('Global extension settings are unavailable');
            const before = structuredClone(settings); for (const key of Object.keys(settings)) delete settings[key]; Object.assign(settings, structuredClone(value));
            try { const trigger = host.saveSettingsDebounced?.(); if (trigger && typeof trigger.then === 'function') await trigger; return { ok: true, scheduled: true, persisted: false }; } catch (error) { for (const key of Object.keys(settings)) delete settings[key]; Object.assign(settings, before); throw error; }
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
        async saveChatSettings(value, identity = null) {
            const context = host.getContext?.(); const metadata = context?.chatMetadata; if (!metadata) throw new Error('Chat metadata is unavailable');
            if (identity && (context.chatId !== identity.chatId || context.chat !== identity.chat || metadata !== identity.metadata || metadata[namespace] !== identity.namespace)) return { ok: false, reason: 'stale-chat' };
            const had = Object.hasOwn(metadata, namespace); const before = structuredClone(metadata[namespace]); metadata[namespace] ??= {}; metadata[namespace].configOverrides = structuredClone(value);
            const transaction = metadata[namespace]; if (identity) identity.namespace = transaction;
            try { await saveStrictChat(); if (identity && (host.getContext?.()?.chatId !== identity.chatId || host.getContext?.()?.chat !== identity.chat || host.getContext?.()?.chatMetadata !== metadata || metadata[namespace] !== transaction)) return { ok: false, reason: 'stale-chat' }; return { ok: true }; }
            catch (error) { if (host.getContext?.()?.chatMetadata === metadata && metadata[namespace] === transaction) { if (had) metadata[namespace] = before; else delete metadata[namespace]; } throw error; }
        },
        listPresetReferences: typeof host.listPresetReferences === 'function' ? id => host.listPresetReferences(id) : undefined,
        countTokens: text => host.countTokens(text),
        canInjectPrompt: typeof host.setExtensionPrompt === 'function',
        canPersist: typeof host.strictSaveChat === 'function',
        canRegisterTools: typeof host.registerTool === 'function',
        canProbeMainTools: typeof host.probeMainTool === 'function',
    };
}
