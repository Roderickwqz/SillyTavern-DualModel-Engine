import { expect, it, vi } from 'vitest';
import { createMainToolProbe } from '../../src/main-tool-probe.js';

function host(overrides = {}) { const listeners = new Set(); const eventSource = { on: vi.fn((_e, fn) => listeners.add(fn)), removeListener: vi.fn((_e, fn) => listeners.delete(fn)) }; return { eventSource, eventTypes: { CHAT_COMPLETION_SETTINGS_READY: 'ready' }, probeNonce: () => 'secret-nonce', isGenerating: () => false, generateRawData: vi.fn(async () => ({ choices: [] })), tools: { isToolCallingSupported: () => true, invokeFunctionTools: vi.fn(async () => ({ errors: [], stealthCalls: [] })) }, emit(data = {}) { for (const fn of [...listeners]) fn(data); return data; }, ...overrides }; }
const definition = { name: 'probe', description: 'p', parameters: {} };
it('injects only forced probe tool and passes nonstream raw to invoke', async () => { const h = host(); h.generateRawData.mockImplementation(async ({ prompt }) => { h.payload = h.emit({ prompt, tools: ['old'] }); return { choices: [{ message: { tool_calls: [] } }] }; }); await createMainToolProbe(h)({ prompt: 'x', definition }); expect(h.payload).toMatchObject({ tools: [{ function: { name: 'probe' } }], tool_choice: { function: { name: 'probe' } } }); expect(h.tools.invokeFunctionTools).toHaveBeenCalledWith(expect.objectContaining({ choices: expect.any(Array) })); expect(h.eventSource.removeListener).toHaveBeenCalled(); });
it('ignores an unrelated normal-generation settings event until its marker arrives', async () => {
    const h = host(); let markerPayload;
    h.generateRawData.mockImplementation(async ({ prompt }) => { const normal = h.emit({ messages: [{ content: '[[dual-model-probe:1:]]' }], tools: ['normal'] }); expect(normal.tools).toEqual(['normal']); markerPayload = h.emit({ messages: [{ content: prompt }], tools: ['old'] }); return { choices: [] }; });
    await createMainToolProbe(h)({ prompt: 'quiet', definition });
    expect(markerPayload).toMatchObject({ tools: [{ function: { name: 'probe' } }], tool_choice: { function: { name: 'probe' } } });
    expect(h.eventSource.removeListener).toHaveBeenCalledTimes(2);
});
it('rejects an empty nonce before registering a settings listener', async () => {
    let valid = false; const h = host({ probeNonce: () => valid ? 'recovered-secret' : '' }); const probe = createMainToolProbe(h);
    await expect(probe({ definition })).rejects.toThrow('nonce');
    expect(h.eventSource.on).not.toHaveBeenCalled();
    valid = true; h.generateRawData.mockImplementation(async ({ prompt }) => { h.emit({ prompt }); return { choices: [] }; });
    await expect(probe({ definition })).resolves.toMatchObject({ supported: true });
});
it('handles busy, unsupported, stream, throws and releases its mutex', async () => {
    const busy = host({ isGenerating: () => true }); await expect(createMainToolProbe(busy)({ definition })).rejects.toThrow('Finish');
    const unsupported = host({ tools: { isToolCallingSupported: () => false } }); await expect(createMainToolProbe(unsupported)({ definition })).resolves.toMatchObject({ supported: false }); expect(unsupported.eventSource.on).not.toHaveBeenCalled();
    const h = host(); h.generateRawData.mockImplementation(async ({ prompt }) => { h.emit({ prompt }); return async function* () { yield { toolCalls: [{ function: { name: 'a' } }] }; yield { toolCalls: [{ function: { name: 'b' } }] }; }; }); const probe = createMainToolProbe(h); await probe({ definition }); expect(h.tools.invokeFunctionTools).toHaveBeenCalledWith([{ function: { name: 'b' } }]);
    let release; h.generateRawData.mockImplementationOnce(() => new Promise(resolve => { release = resolve; })); const first = probe({ definition }); await expect(probe({ definition })).rejects.toThrow('already'); release({ choices: [] }); await first;
    await expect(probe({ definition })).resolves.toMatchObject({ supported: true });
    await expect(createMainToolProbe(host({ generateRawData: vi.fn(async () => { throw new Error('generate bad'); }) }))({ definition })).rejects.toThrow('generate bad');
    const invokeFailure = host(); invokeFailure.generateRawData.mockImplementation(async ({ prompt }) => { invokeFailure.emit({ prompt }); return { choices: [] }; }); invokeFailure.tools.invokeFunctionTools.mockRejectedValueOnce(new Error('invoke bad')); await expect(createMainToolProbe(invokeFailure)({ definition })).rejects.toThrow('invoke bad');
});
