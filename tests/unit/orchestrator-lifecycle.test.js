import { expect, it, vi } from 'vitest';
import { createOrchestrator } from '../../src/orchestrator.js';
import { bootstrap } from '../../src/index.js';

function deps(overrides = {}) {
    const adapter = { events: { CHAT_CHANGED: 'a', GENERATION_AFTER_COMMANDS: 'b', GENERATION_ENDED: 'c', GENERATION_STOPPED: 'd' }, getContext: () => ({ chatId: 'x', chat: [] }), on: vi.fn(), off: vi.fn() };
    return { adapter, queue: { cancelChat: vi.fn(), getStatus: () => ({ state: 'idle', requestId: null }), waitForIdle: async () => {} }, promptInjector: { clear: vi.fn(), refresh: vi.fn() }, recordDiagnostic: vi.fn(), ...overrides };
}

it('attempts every cleanup action when off, cancel, and clear throw', () => {
    const value = deps(); value.adapter.off.mockImplementation(() => { throw new Error('off'); }); value.queue.cancelChat.mockImplementation(() => { throw new Error('cancel'); }); value.promptInjector.clear.mockImplementation(() => { throw new Error('clear'); });
    const subject = createOrchestrator(value); subject.start();
    expect(() => subject.stop()).toThrow('off');
    expect(value.adapter.off).toHaveBeenCalledTimes(4); expect(value.promptInjector.clear).toHaveBeenCalledOnce();
});

it('rolls back every bound listener when a later bind rejects', () => {
    const value = deps(); value.adapter.on.mockImplementation(name => { if (name === 'c') throw new Error('bind'); }); value.adapter.off.mockImplementation(() => { throw new Error('off'); });
    expect(() => createOrchestrator(value).start()).toThrow('bind');
    expect(value.adapter.off).toHaveBeenCalledTimes(2); expect(value.promptInjector.clear).toHaveBeenCalledOnce();
});

it('T8 bootstrap retains init error while attempting all cleanup', async () => {
    const initError = new Error('init'); const value = deps(); const handlers = [];
    value.adapter.on.mockImplementation((_name, handler) => handlers.push(handler)); value.adapter.off.mockImplementation(() => { throw new Error('off'); });
    value.queue.cancelChat.mockImplementation(() => { throw new Error('cancel'); }); value.promptInjector.clear.mockImplementation(() => { throw new Error('clear'); });
    await expect(bootstrap({ adapter: value.adapter, dependencies: { queue: value.queue, promptInjector: value.promptInjector, getConfig: () => { throw initError; }, store: { loadEnvelope: () => ({ ok: false }) }, recordDiagnostic: value.recordDiagnostic } })).rejects.toBe(initError);
    expect(value.adapter.off).toHaveBeenCalledTimes(4); expect(value.queue.cancelChat).toHaveBeenCalledWith('x', 'orchestrator-stopped'); expect(value.promptInjector.clear).toHaveBeenCalledOnce(); expect(handlers).toHaveLength(4);
});

it('T8 refuses restart after an unbind failure to avoid duplicate handlers', () => {
    const value = deps(); value.adapter.off.mockImplementation(() => { throw new Error('off'); }); const subject = createOrchestrator(value);
    subject.start(); expect(() => subject.stop()).toThrow('off'); subject.start();
    expect(value.adapter.on).toHaveBeenCalledTimes(4); expect(value.adapter.off).toHaveBeenCalledTimes(4); expect(value.promptInjector.clear).toHaveBeenCalledOnce();
});
