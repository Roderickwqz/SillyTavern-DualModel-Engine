import { expect, it, vi } from 'vitest';
import { createOrchestrator } from '../../src/orchestrator.js';

function deferred() { let resolve; return { promise: new Promise(value => { resolve = value; }), resolve }; }
function envelope() { return { stateVersion: 0, headRevision: 0, activeSnapshot: { version: 0 }, preset: { id: 'narrative' } }; }
function subject() {
    const contexts = { a: { chatId: 'a', groupId: null, chat: [{ is_user: true, mes: 'old', extra: { dualModelEngine: { messageId: 'u-a' } } }], chatMetadata: { dualModelEngine: envelope() } }, b: { chatId: 'b', groupId: null, chat: [{ is_user: true, mes: 'new', extra: { dualModelEngine: { messageId: 'u-b' } } }], chatMetadata: { dualModelEngine: envelope() } } }; let current = contexts.a; const listeners = new Map(); const first = deferred(); const calls = [];
    const refresh = vi.fn(input => { calls.push(input.hardRuleText ?? 'plain'); return calls.length === 1 ? first.promise : Promise.resolve(); });
    const deps = { adapter: { events: { CHAT_CHANGED: 'chat', GENERATION_AFTER_COMMANDS: 'before', GENERATION_STOPPED: 'stop' }, getContext: () => current, on: (name, fn) => listeners.set(name, fn), off: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); } }, queue: { waitForIdle: async () => {}, getStatus: () => ({}), cancelChat: vi.fn() }, promptInjector: { refresh, clear: vi.fn(() => calls.push('clear')) }, getConfig: () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'narrative', injectionBudget: 1 }), getPreset: () => ({ injection: [] }), store: { loadEnvelope: () => current.chatMetadata.dualModelEngine }, hasProfile: () => true, makeId: (() => { let i = 0; return () => `r${++i}`; })(), adjudicator: { resolveBeforeGeneration: vi.fn(input => Promise.resolve({ injectedText: input.playerText === 'new' ? 'NEW-HARD' : 'OLD-HARD' })) }, recordDiagnostic: vi.fn() };
    const orchestrator = createOrchestrator(deps); orchestrator.start();
    return { contexts, setCurrent: key => { current = contexts[key]; }, listeners, first, calls, refresh, clear: deps.promptInjector.clear, diagnostic: deps.recordDiagnostic, orchestrator };
}

it('host STOP cancels a gated before-generation refresh, clears promptly, and never revives it when the refresh settles', async () => {
    const value = subject(); const before = value.listeners.get('before')('normal'); await vi.waitFor(() => expect(value.refresh).toHaveBeenCalledOnce());
    value.listeners.get('stop')(); await expect(before).resolves.toEqual({ ignored: true, reason: 'generation-cancelled' }); expect(value.clear).toHaveBeenCalledOnce();
    value.first.resolve(); await vi.waitFor(() => expect(value.clear).toHaveBeenCalledTimes(2));
    expect(value.clear).toHaveBeenCalledTimes(2); expect(value.refresh).toHaveBeenCalledOnce(); expect(value.diagnostic.mock.calls.flat()).not.toContain('prompt-refresh-recovery-failed');
    value.orchestrator.stop();
});

it('does not let an old cancelled refresh recover over the new chat hard rule', async () => {
    const value = subject(); const old = value.listeners.get('before')('normal'); await vi.waitFor(() => expect(value.refresh).toHaveBeenCalledOnce());
    value.setCurrent('b'); const changed = value.listeners.get('chat')(); const fresh = value.listeners.get('before')('normal'); value.first.resolve();
    await Promise.all([old, changed, fresh]); await Promise.resolve();
    expect(value.calls).toContain('NEW-HARD'); expect(value.calls.at(-1)).toBe('NEW-HARD'); expect(value.refresh).toHaveBeenCalledTimes(3);
    value.orchestrator.stop();
});

it('stop keeps a released old refresh cleared even when clear itself throws', async () => {
    const value = subject(); value.clear.mockImplementation(() => { throw new Error('clear failure'); }); const old = value.listeners.get('before')('normal'); await vi.waitFor(() => expect(value.refresh).toHaveBeenCalledOnce());
    expect(() => value.orchestrator.stop()).toThrow('clear failure'); value.first.resolve(); await old; await Promise.resolve();
    expect(value.refresh).toHaveBeenCalledOnce(); expect(value.diagnostic).toHaveBeenCalledWith(expect.objectContaining({ reason: 'prompt-clear-failed' }));
});
