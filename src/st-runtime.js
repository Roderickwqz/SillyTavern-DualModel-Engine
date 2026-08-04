import { eventSource, event_types, setExtensionPrompt, generateRawData, isGenerating, main_api, getGeneratingModel, getRequestHeaders } from '/script.js';
import { getContext } from '/scripts/extensions.js';
import { ConnectionManagerRequestService } from '/scripts/extensions/shared.js';
import { ToolManager } from '/scripts/tool-calling.js';
import { getTokenCountAsync } from '/scripts/tokenizers.js';
import { compressRequest } from '/scripts/request-compression.js';
import { createSTAdapter, createStrictChatSaver } from './st-adapter.js';
import { createMainToolProbe } from './main-tool-probe.js';

const probeMainTool = createMainToolProbe({ eventSource, eventTypes: event_types, generateRawData, isGenerating, tools: ToolManager, probeNonce: () => {
    if (typeof globalThis.crypto?.randomUUID !== 'function') throw new Error('Web Crypto randomUUID is unavailable for tool probing');
    return globalThis.crypto.randomUUID();
} });

export function createRuntimeAdapter() {
    return createSTAdapter({
        eventSource,
        eventTypes: event_types,
        getContext,
        strictSaveChat: createStrictChatSaver({ getContext, getRequestHeaders, compressRequest }),
        setExtensionPrompt,
        saveSettingsDebounced: () => getContext().saveSettingsDebounced?.(),
        getSettings: () => getContext().extensionSettings.dualModelEngine ??= {},
        getProfiles: () => ConnectionManagerRequestService.getSupportedProfiles(),
        sendRequest: (...args) => ConnectionManagerRequestService.sendRequest(...args),
        registerTool: definition => ToolManager.registerFunctionTool(definition),
        unregisterTool: name => ToolManager.unregisterFunctionTool(name),
        probeMainTool,
        getMainApiModelLabel: () => `${main_api}:${getGeneratingModel()}`,
        countTokens: getTokenCountAsync,
    });
}
