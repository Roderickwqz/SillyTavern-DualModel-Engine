import "./chunk-TRTQSARU.js";

// src/st-runtime.js
import { eventSource, event_types, setExtensionPrompt, saveSettingsDebounced, extension_settings, generateRawData, isGenerating, main_api, getGeneratingModel } from "/script.js";
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
    probeMainTool: typeof host.probeMainTool === "function" ? (params) => host.probeMainTool(params) : void 0,
    getMainApiModelLabel: typeof host.getMainApiModelLabel === "function" ? () => host.getMainApiModelLabel() : () => null,
    saveChat: () => host.getContext().saveMetadata(),
    saveSettings: () => host.saveSettingsDebounced?.(),
    getSettings: () => host.getSettings?.() ?? {},
    countTokens: (text) => host.countTokens(text),
    canInjectPrompt: typeof host.setExtensionPrompt === "function",
    canPersist: typeof host.getContext?.().saveMetadata === "function",
    canRegisterTools: typeof host.registerTool === "function",
    canProbeMainTools: typeof host.probeMainTool === "function"
  };
}

// src/main-tool-probe.js
function createMainToolProbe(host) {
  let probeInFlight = false;
  return async ({ prompt, definition, responseLength = 32 }) => {
    if (host.isGenerating()) throw new Error("Finish the current generation before probing tools");
    if (probeInFlight) throw new Error("A tool capability probe is already running");
    if (!host.tools.isToolCallingSupported()) return { supported: false, reason: "Current main API/model settings do not support tools" };
    probeInFlight = true;
    const inject = (data) => {
      host.eventSource.removeListener(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject);
      data.tools = [{ type: "function", function: { name: definition.name, description: definition.description, parameters: definition.parameters } }];
      data.tool_choice = { type: "function", function: { name: definition.name } };
    };
    host.eventSource.on(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject);
    try {
      let raw = await host.generateRawData({ prompt, responseLength });
      if (typeof raw === "function") {
        let calls = [];
        for await (const chunk of raw()) if (Array.isArray(chunk?.toolCalls)) calls = chunk.toolCalls;
        raw = calls;
      }
      const invocation = await host.tools.invokeFunctionTools(raw);
      return { supported: invocation.errors.length === 0, invocation };
    } finally {
      host.eventSource.removeListener(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject);
      probeInFlight = false;
    }
  };
}

// src/st-runtime.js
var probeMainTool = createMainToolProbe({ eventSource, eventTypes: event_types, generateRawData, isGenerating, tools: ToolManager });
function createRuntimeAdapter() {
  return createSTAdapter({
    eventSource,
    eventTypes: event_types,
    getContext,
    setExtensionPrompt,
    saveSettingsDebounced,
    getSettings: () => extension_settings.dualModelEngine ??= {},
    getProfiles: () => ConnectionManagerRequestService.getSupportedProfiles(),
    sendRequest: (...args) => ConnectionManagerRequestService.sendRequest(...args),
    registerTool: (definition) => ToolManager.registerFunctionTool(definition),
    unregisterTool: (name) => ToolManager.unregisterFunctionTool(name),
    probeMainTool,
    getMainApiModelLabel: () => `${main_api}:${getGeneratingModel()}`,
    countTokens: getTokenCountAsync
  });
}
export {
  createRuntimeAdapter
};
