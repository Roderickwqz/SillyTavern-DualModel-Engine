import { beforeEach, expect, it, vi } from 'vitest';
import { createUIController } from '../../src/ui/controller.js';
import { renderAudit } from '../../src/ui/audit-tab.js';

function dependencies() {
    const context = { chatId: 'chat-a', chat: [], chatMetadata: { dualModelEngine: { activeSnapshot: { version: 0 }, configOverrides: {} } } };
    const noop = vi.fn(async () => {});
    return { adapter: { getContext: () => context, on: vi.fn(), off: vi.fn(), events: {} }, context, queue: { enqueue: (_id, _request, work) => work(new AbortController().signal), getStatus: () => ({ state: 'idle' }) }, capabilities: {}, getGlobalConfig: () => ({}), getCharacterConfig: () => ({}), getChatConfig: () => ({}), saveGlobalConfig: noop, saveCharacterConfig: noop, saveChatConfig: noop, listProfiles: () => [], listPresets: () => [], rollbackManager: { recalculate: vi.fn() }, rerollSelectedCheck: vi.fn(), applyManualDamage: vi.fn(), resummarizeCurrentBranch: vi.fn(), importPresetFromPicker: vi.fn(), downloadPreset: vi.fn(), downloadRawData: vi.fn() };
}

beforeEach(() => { document.body.innerHTML = '<div id="extensions_settings2"></div>'; });
it('renders stale untrusted audit text without executable elements', () => { const box = document.createElement('div'); renderAudit(box, [{ kind: 'check', status: 'stale', action: '<img src=x>', reason: '<script>x</script>' }]); expect(box.textContent).toContain('<img src=x>'); expect(box.querySelector('img,script')).toBeNull(); });
it.each([['recalculate', x => x.rollbackManager.recalculate], ['reroll', x => x.rerollSelectedCheck], ['apply-damage', x => x.applyManualDamage], ['resummarize', x => x.resummarizeCurrentBranch], ['import-preset', x => x.importPresetFromPicker], ['export-preset', x => x.downloadPreset], ['export-raw', x => x.downloadRawData]])('routes %s once', async (action, spy) => { const deps = dependencies(); const ui = createUIController(deps); await ui.mount(); document.querySelector(`[data-dme-action="${action}"]`).click(); await Promise.resolve(); expect(spy(deps)).toHaveBeenCalledOnce(); });

it('uses schema-compatible damageType input', async () => {
    const deps = dependencies(); const ui = createUIController(deps); await ui.mount();
    document.querySelector('[data-dme-role="damage-type"]').value = 'fire';
    document.querySelector('[data-dme-action="apply-damage"]').click(); await Promise.resolve();
    expect(deps.applyManualDamage).toHaveBeenCalledWith(expect.objectContaining({ damageType: 'fire' }));
});
