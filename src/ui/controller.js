import template from './settings.html?raw';

const MIN_BUDGET = 256;
const MAX_BUDGET = 8192;
const PROFILE_EVENTS = ['CONNECTION_PROFILE_LOADED', 'CONNECTION_PROFILE_CREATED', 'CONNECTION_PROFILE_UPDATED', 'CONNECTION_PROFILE_DELETED'];

function budget(value) { return Math.max(MIN_BUDGET, Math.min(MAX_BUDGET, Number(value) || MIN_BUDGET)); }
function copy(value) { return structuredClone(value ?? {}); }

export function createUIController(deps) {
    let root = null; let status = ''; let profileDiagnostic = ''; let probePending = null; const listeners = [];
    const getContext = () => deps.adapter?.getContext?.() ?? {};
    const diagnostic = () => {
        const reasons = [...(deps.capabilities?.reasons ?? [])];
        const profiles = deps.listProfiles?.() ?? [];
        for (const scope of ['global', 'character', 'chat']) {
            const profileId = ({ global: deps.getGlobalConfig, character: deps.getCharacterConfig, chat: deps.getChatConfig }[scope])?.()?.recorderProfileId;
            if (profileId && !profiles.some(profile => profile.id === profileId)) reasons.push(`Recorder profile is missing: ${profileId}`);
        }
        return profileDiagnostic ? [...reasons, profileDiagnostic] : reasons;
    };
    const setText = (selector, value) => { const node = root?.querySelector(selector); if (node) node.textContent = value ?? ''; };
    function populate(select, values, selected) {
        select.replaceChildren();
        for (const value of values) { const option = document.createElement('option'); option.value = value.id; option.textContent = value.name ?? value.id; select.append(option); }
        select.value = selected ?? '';
    }
    async function render() {
        if (!root) return;
        const profiles = deps.listProfiles?.() ?? []; const presets = deps.listPresets?.() ?? [];
        for (const fieldset of root.querySelectorAll('[data-scope]')) {
            const scope = fieldset.dataset.scope;
            const config = copy(({ global: deps.getGlobalConfig, character: deps.getCharacterConfig, chat: deps.getChatConfig }[scope])?.());
            for (const select of fieldset.querySelectorAll('[data-dme-field="recorderProfileId"]')) populate(select, profiles, config.recorderProfileId);
            for (const select of fieldset.querySelectorAll('[data-dme-field="rulePresetId"]')) populate(select, presets, config.rulePresetId);
            for (const field of fieldset.querySelectorAll('[data-dme-field="adjudication"]')) field.value = config.adjudication ?? 'automatic-tool';
            for (const field of fieldset.querySelectorAll('[data-dme-field="injectionBudget"]')) field.value = String(budget(config.injectionBudget ?? 1200));
            for (const field of fieldset.querySelectorAll('[data-dme-field="enabled"], [data-dme-field="showStatusBar"]')) field.checked = Boolean(config[field.dataset.dmeField]);
            for (const field of fieldset.querySelectorAll('[data-dme-field="updatePolicy"]')) field.value = config.updatePolicy ?? 'after-each-reply';
        }
        const isGroup = Boolean(deps.capabilities?.isGroupChat ?? getContext().groupId);
        const chat = root.querySelector('[data-dme-role="chat-settings"]'); chat.disabled = isGroup;
        setText('[data-dme-role="chat-disabled-reason"]', isGroup ? 'Chat settings are unavailable in group chats.' : '');
        setText('[data-dme-role="task-status"]', status);
        setText('[data-dme-role="diagnostic-reasons"]', diagnostic().join('\n'));
    }
    async function save(scope, patch) {
        if (scope === 'global') return deps.saveGlobalConfig(copy({ ...deps.getGlobalConfig?.(), ...patch }));
        if (scope === 'character') return deps.saveCharacterConfig(copy({ ...deps.getCharacterConfig?.(), ...patch }));
        const chatId = getContext().chatId;
        if (!chatId || deps.capabilities?.isGroupChat || getContext().groupId) return;
        return deps.queue.enqueue(chatId, `settings-${Date.now()}`, async signal => {
            signal.throwIfAborted(); if (getContext().chatId !== chatId || getContext().groupId) return;
            await deps.saveChatConfig(copy({ ...deps.getChatConfig?.(), ...patch }));
            if (getContext().chatId !== chatId) return;
            await deps.onConfigChanged?.();
        });
    }
    async function bindPreset(scope, id) {
        if (scope === 'character') { await deps.bindCharacterPreset?.(id); await deps.onConfigChanged?.(); return; }
        if (scope !== 'chat') return save(scope, { rulePresetId: id });
        const chatId = getContext().chatId; if (!chatId || getContext().groupId) return;
        const initial = await deps.bindChatPreset?.(id, { confirmedReset: false });
        if (initial?.reason === 'preset-reset-required') {
            if (initial.exportRawData) status = `Reset required: ${JSON.stringify(initial.summary)}\nRaw preset export is available.`;
            else status = `Reset required: ${JSON.stringify(initial.summary)}`;
            await render(); if (!await confirmAction({ message: status, summary: initial.summary, exportRawData: initial.exportRawData })) return;
        }
        return deps.queue.enqueue(chatId, `preset-${Date.now()}`, async signal => {
            signal.throwIfAborted(); if (getContext().chatId !== chatId || getContext().groupId) return;
            const result = await deps.bindChatPreset?.(id, { confirmedReset: true });
            if (getContext().chatId === chatId && result?.ok) await deps.onConfigChanged?.();
        });
    }
    async function onChange(event) {
        const field = event.target.dataset.dmeField; if (!field) return;
        const scope = event.target.closest('[data-scope]')?.dataset.scope ?? 'global';
        const value = field === 'injectionBudget' ? budget(event.target.value) : event.target.type === 'checkbox' ? event.target.checked : event.target.value;
        try { if (field === 'rulePresetId') await bindPreset(scope, value); else { await save(scope, { [field]: value }); if (scope !== 'chat') await deps.onConfigChanged?.(); } }
        catch (error) { status = error.message ?? String(error); }
        await render();
    }
    async function onClick(event) {
        if (event.target.dataset.dmeAction !== 'probe-tools' || probePending) return;
        probePending = Promise.resolve(deps.runToolProbe?.()).then(result => deps.saveProbeResult?.(result)).catch(error => { status = error.message ?? String(error); }).finally(() => { probePending = null; });
        await probePending; await render();
    }
    async function reloadProfiles() {
        const profiles = deps.listProfiles?.() ?? [];
        for (const scope of ['global', 'character', 'chat']) {
            const getter = { global: deps.getGlobalConfig, character: deps.getCharacterConfig, chat: deps.getChatConfig }[scope];
            const id = getter?.()?.recorderProfileId;
            if (id && !profiles.some(profile => profile.id === id)) { profileDiagnostic = `Recorder profile is missing: ${id}`; await save(scope, { recorderProfileId: '' }); }
        }
        await render();
    }
    async function confirmAction(details) { return deps.showConfirm ? deps.showConfirm(details) : window.confirm(details.message); }
    async function mount() {
        if (root) return render();
        const host = document.querySelector('#extensions_settings') ?? document.querySelector('#extensions_settings2'); if (!host) return;
        host.querySelector('#dualmodel-settings')?.remove();
        host.insertAdjacentHTML('beforeend', template); root = host.querySelector('#dualmodel-settings:last-child');
        root.addEventListener('change', onChange); root.addEventListener('click', onClick);
        for (const eventName of PROFILE_EVENTS) { const event = deps.adapter?.events?.[eventName]; if (!event) continue; const listener = reloadProfiles; deps.adapter.on?.(event, listener); listeners.push([event, listener]); }
        const chatChanged = deps.adapter?.events?.CHAT_CHANGED;
        if (chatChanged) { deps.adapter.on?.(chatChanged, render); listeners.push([chatChanged, render]); }
        await render();
    }
    function setStatus(value) { status = typeof value === 'string' ? value : JSON.stringify(value); return render(); }
    function destroy() { if (!root) return; root.removeEventListener('change', onChange); root.removeEventListener('click', onClick); for (const [event, listener] of listeners) deps.adapter.off?.(event, listener); listeners.length = 0; root.remove(); root = null; }
    return { mount, render, setStatus, confirmAction, destroy };
}
