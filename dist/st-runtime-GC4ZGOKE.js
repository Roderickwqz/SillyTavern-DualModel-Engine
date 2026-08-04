import "./chunk-TRTQSARU.js";

// src/st-runtime.js
import { eventSource, event_types, setExtensionPrompt, generateRawData, isGenerating, main_api, getGeneratingModel } from "/script.js";
import { getContext } from "/scripts/extensions.js";
import { ConnectionManagerRequestService } from "/scripts/extensions/shared.js";
import { ToolManager } from "/scripts/tool-calling.js";
import { getTokenCountAsync } from "/scripts/tokenizers.js";

// src/st-adapter.js
function createSTAdapter(host) {
  const namespace = "dualModelEngine";
  const getCharacter = () => {
    const context = host.getContext?.();
    return context?.characters?.[context?.characterId] ?? context?.character ?? null;
  };
  return {
    events: host.eventTypes ?? {},
    getContext: () => host.getContext(),
    on: (eventName, handler) => host.eventSource?.on(eventName, handler),
    off: (eventName, handler) => host.eventSource?.removeListener(eventName, handler),
    setPrompt: (key, value, options) => host.setExtensionPrompt?.(key, value, options.position, options.depth, false, options.role),
    clearPrompt: (key, options) => host.setExtensionPrompt?.(key, "", options.position, options.depth, false, options.role),
    listProfiles: () => host.getProfiles?.() ?? [],
    requestProfile: (profileId, messages, maxTokens, options, overridePayload = {}) => host.sendRequest(profileId, messages, maxTokens, options, overridePayload),
    registerTool: typeof host.registerTool === "function" ? (definition) => host.registerTool(definition) : void 0,
    unregisterTool: typeof host.unregisterTool === "function" ? (name) => host.unregisterTool(name) : void 0,
    probeMainTool: typeof host.probeMainTool === "function" ? (params) => host.probeMainTool(params) : void 0,
    getMainApiModelLabel: typeof host.getMainApiModelLabel === "function" ? () => host.getMainApiModelLabel() : () => null,
    saveChat: () => host.getContext().saveMetadata(),
    saveSettings: () => host.saveSettingsDebounced?.(),
    getSettings: () => host.getSettings?.() ?? {},
    getGlobalSettings: () => host.getSettings?.() ?? {},
    async saveGlobalSettings(value) {
      const settings = host.getSettings?.();
      if (!settings || typeof settings !== "object") throw new Error("Global extension settings are unavailable");
      const before = structuredClone(settings);
      for (const key of Object.keys(settings)) delete settings[key];
      Object.assign(settings, structuredClone(value));
      try {
        await host.saveSettingsDebounced?.();
      } catch (error) {
        for (const key of Object.keys(settings)) delete settings[key];
        Object.assign(settings, before);
        throw error;
      }
    },
    getCurrentCharacter: getCharacter,
    async saveCurrentCharacter(value) {
      const character = getCharacter();
      if (!character) throw new Error("Current character is unavailable");
      character.data ??= {};
      character.data.extensions ??= {};
      const had = Object.hasOwn(character.data.extensions, namespace);
      const before = structuredClone(character.data.extensions[namespace]);
      character.data.extensions[namespace] = structuredClone(value);
      try {
        const context = host.getContext?.();
        if (typeof context?.writeExtensionField !== "function") throw new Error("Character extension persistence is unavailable");
        await context.writeExtensionField(context.characterId, namespace, structuredClone(value));
      } catch (error) {
        if (had) character.data.extensions[namespace] = before;
        else delete character.data.extensions[namespace];
        throw error;
      }
    },
    getChatMetadata: () => host.getContext?.()?.chatMetadata ?? null,
    async saveChatSettings(value) {
      const context = host.getContext?.();
      const metadata = context?.chatMetadata;
      if (!metadata) throw new Error("Chat metadata is unavailable");
      const had = Object.hasOwn(metadata, namespace);
      const before = structuredClone(metadata[namespace]);
      metadata[namespace] ??= {};
      metadata[namespace].configOverrides = structuredClone(value);
      try {
        await context.saveMetadata?.();
      } catch (error) {
        if (had) metadata[namespace] = before;
        else delete metadata[namespace];
        throw error;
      }
    },
    listPresetReferences: typeof host.listPresetReferences === "function" ? (id) => host.listPresetReferences(id) : void 0,
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
  let sequence = 0;
  return async ({ prompt, definition, responseLength = 32 }) => {
    if (host.isGenerating()) throw new Error("Finish the current generation before probing tools");
    if (probeInFlight) throw new Error("A tool capability probe is already running");
    if (!host.tools.isToolCallingSupported()) return { supported: false, reason: "Current main API/model settings do not support tools" };
    const nonce = host.probeNonce?.();
    if (typeof nonce !== "string" || !nonce) throw new Error("A non-empty unpredictable probe nonce is required");
    probeInFlight = true;
    const marker = `[[dual-model-probe:${++sequence}:${nonce}]]`;
    let injected = false;
    const inject = (data) => {
      if (!JSON.stringify({ prompt: data?.prompt, messages: data?.messages }).includes(marker)) return;
      injected = true;
      host.eventSource.removeListener(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject);
      data.tools = [{ type: "function", function: { name: definition.name, description: definition.description, parameters: definition.parameters } }];
      data.tool_choice = { type: "function", function: { name: definition.name } };
    };
    host.eventSource.on(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject);
    try {
      let raw = await host.generateRawData({ prompt: `${prompt}
${marker}`, responseLength });
      if (!injected) return { supported: false, reason: "Probe settings hook did not match its request" };
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
var probeMainTool = createMainToolProbe({ eventSource, eventTypes: event_types, generateRawData, isGenerating, tools: ToolManager, probeNonce: () => {
  if (typeof globalThis.crypto?.randomUUID !== "function") throw new Error("Web Crypto randomUUID is unavailable for tool probing");
  return globalThis.crypto.randomUUID();
} });
function createRuntimeAdapter() {
  return createSTAdapter({
    eventSource,
    eventTypes: event_types,
    getContext,
    setExtensionPrompt,
    saveSettingsDebounced: () => getContext().saveSettingsDebounced?.(),
    getSettings: () => getContext().extensionSettings.dualModelEngine ??= {},
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
