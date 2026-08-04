import { eventSource, event_types, setExtensionPrompt, saveSettingsDebounced, extension_settings, generateRawData, isGenerating, main_api, getGeneratingModel } from '/script.js';
import { getContext } from '/scripts/extensions.js';
import { ConnectionManagerRequestService } from '/scripts/extensions/shared.js';
import { ToolManager } from '/scripts/tool-calling.js';
import { getTokenCountAsync } from '/scripts/tokenizers.js';
import { createSTAdapter } from './st-adapter.js';

export function createMainToolProbe(host) {
    let probeInFlight = false;
    return async ({ prompt, definition, responseLength = 32 }) => {
        if (host.isGenerating()) throw new Error('Finish the current generation before probing tools');
        if (probeInFlight) throw new Error('A tool capability probe is already running');
        if (!host.tools.isToolCallingSupported()) return { supported: false, reason: 'Current main API/model settings do not support tools' };
        probeInFlight = true;
        const inject = data => { host.eventSource.removeListener(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject); data.tools = [{ type: 'function', function: { name: definition.name, description: definition.description, parameters: definition.parameters } }]; data.tool_choice = { type: 'function', function: { name: definition.name } }; };
        host.eventSource.on(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject);
        try { let raw = await host.generateRawData({ prompt, responseLength }); if (typeof raw === 'function') { let calls = []; for await (const chunk of raw()) if (Array.isArray(chunk?.toolCalls)) calls = chunk.toolCalls; raw = calls; } const invocation = await host.tools.invokeFunctionTools(raw); return { supported: invocation.errors.length === 0, invocation }; }
        finally { host.eventSource.removeListener(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject); probeInFlight = false; }
    };
}
const probeMainTool = createMainToolProbe({ eventSource, eventTypes: event_types, generateRawData, isGenerating, tools: ToolManager });

export function createRuntimeAdapter() {
    return createSTAdapter({
        eventSource,
        eventTypes: event_types,
        getContext,
        setExtensionPrompt,
        saveSettingsDebounced,
        getSettings: () => extension_settings.dualModelEngine ??= {},
        getProfiles: () => ConnectionManagerRequestService.getSupportedProfiles(),
        sendRequest: (...args) => ConnectionManagerRequestService.sendRequest(...args),
        registerTool: definition => ToolManager.registerFunctionTool(definition),
        unregisterTool: name => ToolManager.unregisterFunctionTool(name),
        probeMainTool,
        getMainApiModelLabel: () => `${main_api}:${getGeneratingModel()}`,
        countTokens: getTokenCountAsync,
    });
}
