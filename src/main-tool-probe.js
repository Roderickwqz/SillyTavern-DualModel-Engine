export function createMainToolProbe(host) {
    let probeInFlight = false; let sequence = 0;
    return async ({ prompt, definition, responseLength = 32 }) => {
        if (host.isGenerating()) throw new Error('Finish the current generation before probing tools');
        if (probeInFlight) throw new Error('A tool capability probe is already running');
        if (!host.tools.isToolCallingSupported()) return { supported: false, reason: 'Current main API/model settings do not support tools' };
        const nonce = host.probeNonce?.();
        if (typeof nonce !== 'string' || !nonce) throw new Error('A non-empty unpredictable probe nonce is required');
        probeInFlight = true;
        const marker = `[[dual-model-probe:${++sequence}:${nonce}]]`;
        let injected = false;
        const inject = data => {
            if (!JSON.stringify({ prompt: data?.prompt, messages: data?.messages }).includes(marker)) return;
            injected = true; host.eventSource.removeListener(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject);
            data.tools = [{ type: 'function', function: { name: definition.name, description: definition.description, parameters: definition.parameters } }]; data.tool_choice = { type: 'function', function: { name: definition.name } };
        };
        host.eventSource.on(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject);
        try { let raw = await host.generateRawData({ prompt: `${prompt}\n${marker}`, responseLength }); if (!injected) return { supported: false, reason: 'Probe settings hook did not match its request' }; if (typeof raw === 'function') { let calls = []; for await (const chunk of raw()) if (Array.isArray(chunk?.toolCalls)) calls = chunk.toolCalls; raw = calls; } const invocation = await host.tools.invokeFunctionTools(raw); return { supported: invocation.errors.length === 0, invocation }; }
        finally { host.eventSource.removeListener(host.eventTypes.CHAT_COMPLETION_SETTINGS_READY, inject); probeInFlight = false; }
    };
}
