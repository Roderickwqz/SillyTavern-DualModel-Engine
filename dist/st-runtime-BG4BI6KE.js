import "./chunk-TRTQSARU.js";

// src/st-runtime.js
import { eventSource, event_types, setExtensionPrompt, saveSettingsDebounced, extension_settings, generateRawData, isGenerating } from "/script.js";
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

// src/st-runtime.js
var probeInFlight = false;
async function probeMainTool({ prompt, definition, responseLength = 32 }) {
  if (isGenerating()) throw new Error("Finish the current generation before probing tools");
  if (probeInFlight) throw new Error("A tool capability probe is already running");
  if (!ToolManager.isToolCallingSupported()) return { supported: false, reason: "Current main API/model settings do not support tools" };
  probeInFlight = true;
  const inject = (data) => {
    eventSource.removeListener(event_types.CHAT_COMPLETION_SETTINGS_READY, inject);
    data.tools = [{ type: "function", function: { name: definition.name, description: definition.description, parameters: definition.parameters } }];
    data.tool_choice = { type: "function", function: { name: definition.name } };
  };
  eventSource.on(event_types.CHAT_COMPLETION_SETTINGS_READY, inject);
  try {
    let raw = await generateRawData({ prompt, responseLength });
    if (typeof raw === "function") {
      let toolCalls = [];
      for await (const chunk of raw()) if (Array.isArray(chunk?.toolCalls)) toolCalls = chunk.toolCalls;
      raw = toolCalls;
    }
    const invocation = await ToolManager.invokeFunctionTools(raw);
    return { supported: invocation.errors.length === 0, invocation };
  } finally {
    eventSource.removeListener(event_types.CHAT_COMPLETION_SETTINGS_READY, inject);
    probeInFlight = false;
  }
}
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
    probeMainTool,
    countTokens: getTokenCountAsync
  });
}
export {
  createRuntimeAdapter
};
