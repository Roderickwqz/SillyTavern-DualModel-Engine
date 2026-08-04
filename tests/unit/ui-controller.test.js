import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createChatTaskQueue } from '../../src/task-queue.js';
import { createUIController } from '../../src/ui/controller.js';

function dependencies(options = {}) {
    const context = { chatId: 'chat-a', groupId: options.isGroupChat ? 'group-a' : null, chatMetadata: { dualModelEngine: { configOverrides: {} } }, character: { data: { extensions: { dualModelEngine: {} } } } };
    return {
        context, adapter: { getContext: () => context, on: vi.fn(), off: vi.fn(), events: { CONNECTION_PROFILE_LOADED: 'loaded', CONNECTION_PROFILE_CREATED: 'created', CONNECTION_PROFILE_UPDATED: 'updated', CONNECTION_PROFILE_DELETED: 'deleted', CHAT_CHANGED: 'chat-changed' } },
        queue: options.queue ?? createChatTaskQueue(),
        getGlobalConfig: () => ({ recorderProfileId: options.profileId ?? 'profile-a', rulePresetId: 'narrative', adjudication: 'automatic-tool', injectionBudget: 1200 }),
        getCharacterConfig: () => context.character.data.extensions.dualModelEngine,
        getChatConfig: () => context.chatMetadata.dualModelEngine.configOverrides,
        saveGlobalConfig: vi.fn(async () => {}), saveCharacterConfig: vi.fn(async () => {}), saveChatConfig: vi.fn(async () => {}),
        listProfiles: () => options.profiles ?? [{ id: 'profile-a', name: 'Profile A' }, { id: 'profile-b', name: '<b>Profile B</b>' }],
        listPresets: () => [{ id: 'narrative', name: 'Narrative' }, { id: 'custom-a', name: '<i>Custom</i>' }],
        capabilities: { isGroupChat: Boolean(options.isGroupChat), reasons: options.diagnosticReason ? [options.diagnosticReason] : [] },
        onConfigChanged: vi.fn(async () => {}), saveProbeResult: vi.fn(async () => options.probeResult ?? { supported: true }), runToolProbe: vi.fn(async () => options.probeResult ?? { supported: true }),
        bindChatPreset: vi.fn(async () => ({ ok: true })), bindCharacterPreset: vi.fn(async () => {}), exportPreset: vi.fn(() => '{}'), showConfirm: vi.fn(async () => true),
    };
}

describe('UI controller', () => {
    beforeEach(() => { document.body.innerHTML = '<div id="extensions_settings2"></div>'; });

    it('persists selected Recorder profile globally and renders diagnostics as text', async () => {
        const deps = dependencies({ diagnosticReason: '<img src=x onerror=alert(1)>' });
        const ui = createUIController(deps); await ui.mount();
        const field = document.querySelector('[data-dme-field="recorderProfileId"]'); field.value = 'profile-b'; field.dispatchEvent(new globalThis.Event('change', { bubbles: true }));
        await Promise.resolve();
        expect(deps.saveGlobalConfig).toHaveBeenCalledWith(expect.objectContaining({ recorderProfileId: 'profile-b' }));
        expect(document.querySelector('[data-dme-role="diagnostic-reasons"]').textContent).toContain('<img');
        expect(document.querySelector('[data-dme-role="diagnostic-reasons"] img')).toBeNull();
    });

    it('disables chat writes for groups and cleans up on destroy', async () => {
        const deps = dependencies({ isGroupChat: true }); const ui = createUIController(deps); await ui.mount(); await ui.mount();
        expect(document.querySelector('[data-dme-role="chat-settings"]').disabled).toBe(true);
        expect(document.querySelector('[data-dme-role="chat-disabled-reason"]').textContent).toBe('Chat settings are unavailable in group chats.');
        expect(document.querySelectorAll('#dualmodel-settings')).toHaveLength(1); ui.destroy();
        expect(document.querySelector('#dualmodel-settings')).toBeNull(); expect(deps.adapter.off).toHaveBeenCalledTimes(5);
    });

    it('stores a tool probe only once while it is running', async () => {
        let finish; const pending = new Promise(resolve => { finish = resolve; }); const deps = dependencies(); deps.runToolProbe.mockReturnValue(pending);
        const ui = createUIController(deps); await ui.mount(); const button = document.querySelector('[data-dme-action="probe-tools"]'); button.click(); button.click(); finish({ supported: true }); await pending; await Promise.resolve();
        expect(deps.runToolProbe).toHaveBeenCalledTimes(1); expect(deps.saveProbeResult).toHaveBeenCalledTimes(1);
    });

    it('serializes chat configuration changes behind existing chat work', async () => {
        let release; const blocker = new Promise(resolve => { release = resolve; }); const deps = dependencies(); deps.queue.enqueue('chat-a', 'recorder', () => blocker);
        const ui = createUIController(deps); await ui.mount(); const field = document.querySelector('[data-scope="chat"] [data-dme-field="adjudication"]'); field.value = 'manual'; field.dispatchEvent(new globalThis.Event('change', { bubbles: true }));
        await Promise.resolve(); expect(deps.saveChatConfig).not.toHaveBeenCalled(); release(); await deps.queue.waitForIdle('chat-a');
        expect(deps.saveChatConfig).toHaveBeenCalledWith(expect.objectContaining({ adjudication: 'manual' }), expect.objectContaining({ chatId: 'chat-a' }));
    });

    it('persists enabled as a boolean at each scope', async () => {
        const deps = dependencies(); const ui = createUIController(deps); await ui.mount();
        const field = document.querySelector('[data-scope="chat"] [data-dme-field="enabled"]'); field.checked = true; field.dispatchEvent(new globalThis.Event('change', { bubbles: true }));
        await deps.queue.waitForIdle('chat-a'); expect(deps.saveChatConfig).toHaveBeenCalledWith(expect.objectContaining({ enabled: true }), expect.objectContaining({ chatId: 'chat-a' }));
    });

    it('passes untrusted reset details to confirmation only in a text node', async () => {
        const deps = dependencies(); deps.bindChatPreset.mockResolvedValueOnce({ ok: false, reason: 'preset-reset-required', summary: { reason: '</p><img src=x onerror=alert(1)>' } });
        const ui = createUIController(deps); await ui.mount(); const field = document.querySelector('[data-scope="chat"] [data-dme-field="rulePresetId"]'); field.value = 'custom-a'; field.dispatchEvent(new globalThis.Event('change', { bubbles: true })); await Promise.resolve(); await Promise.resolve();
        const details = deps.showConfirm.mock.calls[0][0]; expect(details.message).not.toContain('<img'); expect(details.content.textContent).toContain('<img'); expect(details.content.querySelector('img')).toBeNull();
    });

    it('uses current group state instead of a stale capability snapshot', async () => {
        const deps = dependencies(); const ui = createUIController(deps); await ui.mount(); deps.context.groupId = 'new-group'; await ui.render();
        expect(document.querySelector('[data-dme-role="chat-settings"]').disabled).toBe(true);
    });

    it('refreshes after a confirmed reset replaces the current envelope', async () => {
        const deps = dependencies(); deps.bindChatPreset.mockImplementation(async (_id, options) => { if (!options.confirmedReset) return { ok: false, reason: 'preset-reset-required', summary: {} }; deps.context.chatMetadata.dualModelEngine = { configOverrides: {} }; return { ok: true }; });
        const ui = createUIController(deps); await ui.mount(); const field = document.querySelector('[data-scope="chat"] [data-dme-field="rulePresetId"]'); field.value = 'custom-a'; field.dispatchEvent(new globalThis.Event('change', { bubbles: true })); await new Promise(resolve => window.setTimeout(resolve)); await deps.queue.waitForIdle('chat-a');
        expect(deps.onConfigChanged).toHaveBeenCalledOnce();
    });
});
