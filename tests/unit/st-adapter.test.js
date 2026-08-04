import { describe, expect, it, vi } from 'vitest';
import { createSTAdapter, createStrictChatSaver } from '../../src/st-adapter.js';

describe('createSTAdapter', () => {
    it('sends Recorder requests without changing the selected main profile', async () => {
        const sendRequest = vi.fn().mockResolvedValue({ content: '{"base_version":0,"operations":[]}' });
        const host = {
            getContext: () => ({ chatId: 'chat-a', groupId: null, chatMetadata: {}, chat: [] }),
            sendRequest,
            getProfiles: () => [{ id: 'recorder', name: 'Recorder' }],
        };
        const adapter = createSTAdapter(host);

        await adapter.requestProfile('recorder', [{ role: 'user', content: 'state' }], 800, { stream: false });

        expect(sendRequest).toHaveBeenCalledWith(
            'recorder',
            expect.any(Array),
            800,
            expect.objectContaining({ stream: false }),
            {},
        );
    });

    it('does not expose registration operations when the host has no real tool API', () => {
        const adapter = createSTAdapter({ getContext: () => ({}) });
        expect(adapter.registerTool).toBeUndefined();
        expect(adapter.unregisterTool).toBeUndefined();
    });

    it('exposes the host seam for enumerating persisted preset references', () => {
        const listPresetReferences = vi.fn(() => [{ type: 'archived-chat', id: 'old' }]);
        const adapter = createSTAdapter({ getContext: () => ({}), listPresetReferences });
        expect(adapter.listPresetReferences('custom-a')).toEqual([{ type: 'archived-chat', id: 'old' }]);
        expect(listPresetReferences).toHaveBeenCalledWith('custom-a');
    });

    it('persists only the character namespace through writeExtensionField and restores it on failure', async () => {
        const character = { data: { extensions: { dualModelEngine: { enabled: false }, other: { keep: true } } } }; const context = { characterId: 'c1', characters: { c1: character }, writeExtensionField: vi.fn().mockRejectedValue(new Error('disk')) };
        const adapter = createSTAdapter({ getContext: () => context });
        await expect(adapter.saveCurrentCharacter({ enabled: true })).rejects.toThrow('disk');
        expect(context.writeExtensionField).toHaveBeenCalledWith('c1', 'dualModelEngine', { enabled: true }); expect(character.data.extensions).toEqual({ dualModelEngine: { enabled: false }, other: { keep: true } });
    });

    it('rejects character persistence when the official extension-field API is absent', async () => {
        const adapter = createSTAdapter({ getContext: () => ({ characterId: 'c1', characters: { c1: { data: {} } } }) });
        await expect(adapter.saveCurrentCharacter({ enabled: true })).rejects.toThrow('Character extension persistence is unavailable');
    });

    it('does not restore stale chat metadata after saveMetadata fails', async () => {
        const original = { dualModelEngine: { configOverrides: { enabled: false } } }; let context = { chatId: 'a', chat: [], chatMetadata: original };
        const strictSaveChat = async () => { context = { chatId: 'a', chat: [], chatMetadata: { dualModelEngine: { concurrent: true } } }; throw new Error('disk'); };
        const adapter = createSTAdapter({ getContext: () => context, strictSaveChat });
        await expect(adapter.saveChatSettings({ enabled: true })).rejects.toThrow('disk'); expect(context.chatMetadata.dualModelEngine).toEqual({ concurrent: true });
    });

    it('reports global debounced saving as scheduled rather than durable', async () => {
        const settings = { enabled: false, customPresets: [] }; const saveSettingsDebounced = vi.fn(); const adapter = createSTAdapter({ getContext: () => ({}), getSettings: () => settings, saveSettingsDebounced });
        await expect(adapter.saveGlobalSettings({ enabled: true })).resolves.toEqual({ ok: true, scheduled: true, persisted: false }); expect(settings).toEqual({ enabled: true }); expect(saveSettingsDebounced).toHaveBeenCalledOnce();
    });

    it('fails closed when the host exposes only SillyTavern metadata saving', async () => {
        const saveMetadata = vi.fn().mockResolvedValue(undefined);
        const adapter = createSTAdapter({ getContext: () => ({ chatMetadata: {}, saveMetadata }) });
        await expect(adapter.saveChat()).rejects.toThrow('Strict chat persistence is unavailable');
        expect(adapter.canPersist).toBe(false);
        expect(saveMetadata).not.toHaveBeenCalled();
    });

    it('uses strict persistence for chat settings instead of swallowed metadata saving', async () => {
        const strictSaveChat = vi.fn().mockRejectedValue(new Error('integrity'));
        const metadata = { dualModelEngine: { configOverrides: { enabled: false } } };
        const saveMetadata = vi.fn().mockResolvedValue(undefined);
        const adapter = createSTAdapter({ getContext: () => ({ chatMetadata: metadata, saveMetadata }), strictSaveChat });
        await expect(adapter.saveChatSettings({ enabled: true })).rejects.toThrow('integrity');
        expect(metadata.dualModelEngine.configOverrides).toEqual({ enabled: false });
        expect(strictSaveChat).toHaveBeenCalledOnce();
        expect(saveMetadata).not.toHaveBeenCalled();
    });

    it('serializes a SillyTavern character chat save exactly and accepts only an OK response', async () => {
        const context = {
            groupId: null,
            characterId: 0,
            chatId: 'adventure.jsonl',
            characters: [{ name: 'Ava', avatar: 'ava.png' }],
            chatMetadata: { dualModelEngine: { stateVersion: 2 } },
            chat: [{ is_user: true, mes: 'hello' }],
        };
        const getRequestHeaders = vi.fn(() => ({ 'X-CSRF-Token': 'csrf' }));
        const compressRequest = vi.fn(async request => ({ ...request, compressed: true }));
        const fetch = vi.fn().mockResolvedValue({ ok: true });
        const save = createStrictChatSaver({ getContext: () => context, getRequestHeaders, compressRequest, fetch });

        await expect(save()).resolves.toBeUndefined();
        expect(compressRequest).toHaveBeenCalledWith({
            method: 'POST', cache: 'no-cache', headers: { 'X-CSRF-Token': 'csrf' },
            body: JSON.stringify({
                ch_name: 'Ava', file_name: 'adventure.jsonl',
                chat: [{ chat_metadata: context.chatMetadata, user_name: 'unused', character_name: 'unused' }, ...context.chat],
                avatar_url: 'ava.png', force: false,
            }),
        });
        expect(fetch).toHaveBeenCalledWith('/api/chats/save', expect.objectContaining({ compressed: true }));
    });

    it.each([
        ['a non-OK response', () => Promise.resolve({ ok: false, statusText: 'Conflict' })],
        ['a rejected request', () => Promise.reject(new Error('network down'))],
    ])('propagates %s from the strict same-origin chat saver', async (_label, fetchResult) => {
        const context = { groupId: null, characterId: 0, chatId: 'chat.jsonl', characters: [{ name: 'Ava', avatar: 'ava.png' }], chatMetadata: {}, chat: [] };
        const fetch = vi.fn(fetchResult);
        const save = createStrictChatSaver({ getContext: () => context, getRequestHeaders: () => ({}), compressRequest: async request => request, fetch });
        await expect(save()).rejects.toThrow(_label === 'a non-OK response' ? 'Conflict' : 'network down');
        expect(fetch).toHaveBeenCalledWith('/api/chats/save', expect.any(Object));
    });

    it('rejects group chats before any strict persistence request', async () => {
        const fetch = vi.fn();
        const save = createStrictChatSaver({ getContext: () => ({ groupId: 'group-a' }), getRequestHeaders: () => ({}), compressRequest: vi.fn(), fetch });
        await expect(save()).rejects.toThrow('group chat');
        expect(fetch).not.toHaveBeenCalled();
    });
});
