import { expect, it, vi } from 'vitest';
import { createOrchestrator } from '../../src/orchestrator.js';

it('keeps prompt refresh calls serialized after a delayed refresh', async () => {
    let release; const refresh = vi.fn(() => new Promise(resolve => { release = resolve; })); const context = { chatId: 'a', groupId: null, chat: [], chatMetadata: { dualModelEngine: { headRevision: 0, stateVersion: 0, activeSnapshot: { version: 0 }, preset: { id: 'narrative' } } } };
    const subject = createOrchestrator({ adapter: { getContext: () => context, events: {} }, queue: { waitForIdle: async () => {}, getStatus: () => ({}), cancelChat: () => {} }, promptInjector: { refresh, clear: vi.fn() }, getConfig: () => ({ enabled: true, injectionBudget: 1 }), getPreset: () => ({ injection: [] }), store: { loadEnvelope: () => context.chatMetadata.dualModelEngine }, hasProfile: () => true });
    const pending = subject.initializeChat(); await vi.waitFor(() => expect(release).toBeTypeOf('function')); release(); await pending; expect(refresh).toHaveBeenCalledOnce();
});
