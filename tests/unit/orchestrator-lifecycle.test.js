import { expect, it, vi } from 'vitest';
import { createOrchestrator } from '../../src/orchestrator.js';
import { bootstrap } from '../../src/index.js';
import { createOrchestratorTestDependencies } from '../fixtures/fake-host.js';

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
    expect(value.adapter.off).toHaveBeenCalledTimes(5); expect(value.queue.cancelChat).toHaveBeenCalledWith('x', 'orchestrator-stopped'); expect(value.promptInjector.clear).toHaveBeenCalledOnce(); expect(handlers).toHaveLength(5);
});

it('T8 refuses restart after an unbind failure to avoid duplicate handlers', () => {
    const value = deps(); value.adapter.off.mockImplementation(() => { throw new Error('off'); }); const subject = createOrchestrator(value);
    subject.start(); expect(() => subject.stop()).toThrow('off'); subject.start();
    expect(value.adapter.on).toHaveBeenCalledTimes(4); expect(value.adapter.off).toHaveBeenCalledTimes(4); expect(value.promptInjector.clear).toHaveBeenCalledOnce();
});

it('keeps an existing snapshot readable and disables writes when its pinned preset is missing', async () => {
    const value = deps({
        adapter: { events: {}, getContext: () => ({ chatId: 'x', groupId: null, chat: [] }), on: vi.fn(), off: vi.fn() },
        getConfig: () => ({ enabled: true, rulePresetId: 'missing', injectionBudget: 100 }),
        hasProfile: () => true,
        getPreset: () => { throw new Error('Preset not found: missing'); },
        store: { loadEnvelope: () => ({ stateVersion: 4, headRevision: 9, activeSnapshot: { version: 4, notes: ['still readable'] }, preset: { id: 'missing' } }) },
    });
    const subject = createOrchestrator(value);
    await expect(subject.initializeChat()).resolves.toEqual({ enabled: false, reason: 'missing-preset' });
    await expect(subject.beforeGeneration('normal')).resolves.toEqual({ ignored: true, reason: 'missing-preset' });
    expect(value.promptInjector.clear).toHaveBeenCalled();
    expect(value.recordDiagnostic).toHaveBeenCalledWith(expect.objectContaining({ reason: 'missing-preset', presetId: 'missing' }));
});

it('pins generation validation config to the existing envelope preset', async () => {
    const value = createOrchestratorTestDependencies({ chat: [{ is_user: true, mes: 'go', extra: { dualModelEngine: { messageId: 'u1' } } }] });
    const envelope = { stateVersion: 0, headRevision: 2, activeSnapshot: { version: 0, inventory: [] }, preset: { id: 'custom-a', version: 1 } };
    value.store.loadEnvelope = () => envelope; value.getConfig = () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'custom-b', injectionBudget: 100 });
    value.getPreset = id => ({ id, allowedPaths: [`/${id}`], lockedPaths: ['/version'], injection: [] });
    const subject = createOrchestrator(value);
    await expect(subject.beforeGeneration('normal')).resolves.toMatchObject({ ok: true });
    expect(subject.getActiveGeneration()).toMatchObject({ preset: { id: 'custom-a' }, effectiveConfig: { rulePresetId: 'custom-a' } });
});

it('pins replay validation to the existing envelope preset', async () => {
    const assistant = { is_user: false, mes: 'answer', swipe_id: 0, extra: { dualModelEngine: { messageId: 'a1' } }, swipe_info: [{ extra: {} }] };
    const branch = { branchId: 'b1', segments: [{ checks: [] }] }; const value = createOrchestratorTestDependencies({ chat: [{ is_user: true, mes: 'go', extra: { dualModelEngine: { messageId: 'u1' } } }, assistant] });
    const envelope = { stateVersion: 0, headRevision: 2, activeSnapshot: { version: 0 }, preset: { id: 'custom-a', version: 1 } };
    value.store.loadEnvelope = () => envelope; value.store.getBranch = () => branch; value.store.commitSegment = vi.fn(async () => ({ ok: true }));
    value.getConfig = () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'custom-b', injectionBudget: 100 }); value.getPreset = id => ({ id, allowedPaths: [`/${id}`], lockedPaths: ['/version'], injection: [] });
    value.modelService.requestPatch = vi.fn(async () => ({ patch: { base_version: 0, operations: [] } })); value.validator.validatePatch = vi.fn(() => ({ ok: true }));
    const subject = createOrchestrator(value);
    await expect(subject.replayTurn({ messageIndex: 1, swipeId: 0, baseSnapshot: { version: 0 } })).resolves.toMatchObject({ ok: true });
    expect(value.validator.validatePatch).toHaveBeenCalledWith('custom-a', expect.anything(), expect.objectContaining({ allowedPaths: ['/custom-a'] }));
});
