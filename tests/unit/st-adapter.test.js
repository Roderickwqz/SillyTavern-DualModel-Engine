import { describe, expect, it, vi } from 'vitest';
import { createSTAdapter } from '../../src/st-adapter.js';

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
        const original = { dualModelEngine: { configOverrides: { enabled: false } } }; let context = { chatId: 'a', chat: [], chatMetadata: original, saveMetadata: async () => { context = { chatId: 'a', chat: [], chatMetadata: { dualModelEngine: { concurrent: true } } }; throw new Error('disk'); } };
        const adapter = createSTAdapter({ getContext: () => context });
        await expect(adapter.saveChatSettings({ enabled: true })).rejects.toThrow('disk'); expect(context.chatMetadata.dualModelEngine).toEqual({ concurrent: true });
    });
});
