import "./chunk-TRTQSARU.js";

// src/st-runtime.js
import { eventSource, event_types, setExtensionPrompt, saveSettingsDebounced, extension_settings } from "/script.js";
import { getContext } from "/scripts/extensions.js";
import { ConnectionManagerRequestService } from "/scripts/extensions/shared.js";
import { ToolManager } from "/scripts/tool-calling.js";
import { getTokenCountAsync } from "/scripts/tokenizers.js";

// src/st-adapter.js
function createSTAdapter(host) {
  return {
    events: host.eventTypes ?? {},
    getContext: () => host.getContext(),
    on: (eventName, handler) => host.eventSource?.on(eventName, handler),
    off: (eventName, handler) => host.eventSource?.removeListener(eventName, handler),
    setPrompt: (key, value, options) => host.setExtensionPrompt?.(key, value, options.position, options.depth, false, options.role),
    clearPrompt: (key, options) => host.setExtensionPrompt?.(key, "", options.position, options.depth, false, options.role),
    listProfiles: () => host.getProfiles?.() ?? [],
    requestProfile: (profileId, messages, maxTokens, options, overridePayload = {}) => host.sendRequest(profileId, messages, maxTokens, options, overridePayload),
    registerTool: (definition) => host.registerTool?.(definition),
    unregisterTool: (name) => host.unregisterTool?.(name),
    saveChat: () => host.getContext().saveMetadata(),
    saveSettings: () => host.saveSettingsDebounced?.(),
    getSettings: () => host.getSettings?.() ?? {},
    countTokens: (text) => host.countTokens(text),
    canInjectPrompt: typeof host.setExtensionPrompt === "function",
    canPersist: typeof host.getContext?.().saveMetadata === "function",
    canRegisterTools: typeof host.registerTool === "function"
  };
}

// src/st-runtime.js
function createRuntimeAdapter() {
  return createSTAdapter({
    eventSource,
    eventTypes: event_types,
    getContext,
    setExtensionPrompt,
    saveSettingsDebounced,
    getSettings: () => extension_settings.dualModelEngine ?? {},
    getProfiles: () => ConnectionManagerRequestService.getSupportedProfiles(),
    sendRequest: (...args) => ConnectionManagerRequestService.sendRequest(...args),
    registerTool: (definition) => ToolManager.registerFunctionTool(definition),
    unregisterTool: (name) => ToolManager.unregisterFunctionTool(name),
    countTokens: getTokenCountAsync
  });
}
export {
  createRuntimeAdapter
};
