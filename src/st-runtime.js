import { eventSource, event_types, setExtensionPrompt, saveSettingsDebounced, extension_settings } from '/script.js';
import { getContext } from '/scripts/extensions.js';
import { ConnectionManagerRequestService } from '/scripts/extensions/shared.js';
import { ToolManager } from '/scripts/tool-calling.js';
import { getTokenCountAsync } from '/scripts/tokenizers.js';
import { createSTAdapter } from './st-adapter.js';

export function createRuntimeAdapter() {
    return createSTAdapter({
        eventSource,
        eventTypes: event_types,
        getContext,
        setExtensionPrompt,
        saveSettingsDebounced,
        getSettings: () => extension_settings.dualModelEngine ?? {},
        getProfiles: () => ConnectionManagerRequestService.getSupportedProfiles(),
        sendRequest: (...args) => ConnectionManagerRequestService.sendRequest(...args),
        registerTool: definition => ToolManager.registerFunctionTool(definition),
        unregisterTool: name => ToolManager.unregisterFunctionTool(name),
        countTokens: getTokenCountAsync,
    });
}
