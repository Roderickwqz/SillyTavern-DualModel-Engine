import { expect, it, vi } from 'vitest';
import { createMainToolProbe } from '../../src/main-tool-probe.js';

function host(overrides = {}) { const listeners = new Set(); const eventSource = { on: vi.fn((_e, fn) => listeners.add(fn)), removeListener: vi.fn((_e, fn) => listeners.delete(fn)) }; return { eventSource, eventTypes: { CHAT_COMPLETION_SETTINGS_READY: 'ready' }, isGenerating: () => false, generateRawData: vi.fn(async () => ({ choices: [] })), tools: { isToolCallingSupported: () => true, invokeFunctionTools: vi.fn(async () => ({ errors: [], stealthCalls: [] })) }, emit(data = {}) { for (const fn of [...listeners]) fn(data); return data; }, ...overrides }; }
const definition = { name: 'probe', description: 'p', parameters: {} };
it('injects only forced probe tool and passes nonstream raw to invoke', async () => { const h = host(); h.generateRawData.mockImplementation(async () => { h.payload = h.emit({ tools: ['old'] }); return { choices: [{ message: { tool_calls: [] } }] }; }); await createMainToolProbe(h)({ prompt: 'x', definition }); expect(h.payload).toMatchObject({ tools: [{ function: { name: 'probe' } }], tool_choice: { function: { name: 'probe' } } }); expect(h.tools.invokeFunctionTools).toHaveBeenCalledWith(expect.objectContaining({ choices: expect.any(Array) })); expect(h.eventSource.removeListener).toHaveBeenCalled(); });
it('handles busy, unsupported, stream, throws and releases its mutex', async () => {
    const busy = host({ isGenerating: () => true }); await expect(createMainToolProbe(busy)({ definition })).rejects.toThrow('Finish');
    const unsupported = host({ tools: { isToolCallingSupported: () => false } }); await expect(createMainToolProbe(unsupported)({ definition })).resolves.toMatchObject({ supported: false }); expect(unsupported.eventSource.on).not.toHaveBeenCalled();
    const h = host(); h.generateRawData.mockResolvedValue(async function* () { yield { toolCalls: [{ function: { name: 'a' } }] }; yield { toolCalls: [{ function: { name: 'b' } }] }; }); const probe = createMainToolProbe(h); await probe({ definition }); expect(h.tools.invokeFunctionTools).toHaveBeenCalledWith([{ function: { name: 'b' } }]);
    let release; h.generateRawData.mockImplementationOnce(() => new Promise(resolve => { release = resolve; })); const first = probe({ definition }); await expect(probe({ definition })).rejects.toThrow('already'); release({ choices: [] }); await first;
    await expect(probe({ definition })).resolves.toMatchObject({ supported: true });
    await expect(createMainToolProbe(host({ generateRawData: vi.fn(async () => { throw new Error('generate bad'); }) }))({ definition })).rejects.toThrow('generate bad');
    const invokeFailure = host(); invokeFailure.tools.invokeFunctionTools.mockRejectedValueOnce(new Error('invoke bad')); await expect(createMainToolProbe(invokeFailure)({ definition })).rejects.toThrow('invoke bad');
});
