import { beforeEach, describe, expect, it, vi } from 'vitest';

const runtimeAdapter = {
    getContext: vi.fn(() => ({ groupId: null })),
    listProfiles: vi.fn(() => [{ id: 'recorder', name: 'Recorder' }]),
    canInjectPrompt: true,
    canPersist: true,
    canRegisterTools: true,
    on: vi.fn(),
    off: vi.fn(),
    saveChat: vi.fn(),
    saveSettings: vi.fn(),
};
const createRuntimeAdapter = vi.fn(() => runtimeAdapter);

vi.mock('../../src/st-runtime.js', () => ({ createRuntimeAdapter }));

import { bootstrap } from '../../src/index.js';

describe('bootstrap', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('constructs and starts the injected adapter orchestration without persisting', async () => {
        const adapter = {
            getContext: vi.fn(() => ({ groupId: null })),
            listProfiles: vi.fn(() => [{ id: 'recorder', name: 'Recorder' }]),
            canInjectPrompt: true,
            canPersist: true,
            canRegisterTools: true,
            on: vi.fn(),
            off: vi.fn(),
            saveChat: vi.fn(),
            saveSettings: vi.fn(),
        };

        const result = await bootstrap({ adapter });

        expect(result).toMatchObject({
            name: 'dualModelEngine',
            adapter,
            capabilities: {
                supported: true,
                isGroupChat: false,
                profiles: [{ id: 'recorder', name: 'Recorder' }],
                toolApiAvailable: true,
                promptInjectionAvailable: true,
                persistenceAvailable: true,
                reasons: [],
            },
        });
        expect(createRuntimeAdapter).not.toHaveBeenCalled();
        expect(adapter.on).not.toHaveBeenCalled();
        expect(adapter.off).not.toHaveBeenCalled();
        expect(adapter.saveChat).not.toHaveBeenCalled();
        expect(adapter.saveSettings).not.toHaveBeenCalled();
        expect(result.orchestrator).toBeDefined();
    });

    it('loads the runtime adapter only when one is not injected', async () => {
        const result = await bootstrap();

        expect(createRuntimeAdapter).toHaveBeenCalledOnce();
        expect(result.adapter).toBe(runtimeAdapter);
        expect(result.capabilities.supported).toBe(true);
    });

    it('uses chat configOverrides to make the default rollback swipe handler writable over disabled global settings', async () => {
        const listeners = new Map(); const branch = { branchId: 'branch-0', baseSnapshot: { version: 0 }, baseStateVersion: 0, segments: [{ postSnapshot: { version: 1 } }] };
        const message = { swipe_id: 0, extra: { dualModelEngine: { messageId: 'm1', branch: structuredClone(branch) } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'm1', branch } } }] };
        const envelope = { stateVersion: 0, headRevision: 0, activeSnapshot: { version: 0 }, configOverrides: { enabled: true }, preset: { id: 'narrative' } };
        const restoreBranch = vi.fn(async () => ({ ok: true }));
        const adapter = { events: { MESSAGE_SWIPED: 'swiped', CHAT_CHANGED: 'chat', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', GENERATION_STOPPED: 'stopped' }, getContext: () => ({ chatId: 'chat-a', groupId: null, chat: [message], chatMetadata: { dualModelEngine: envelope } }), getSettings: () => ({ enabled: false }), listProfiles: () => [{ id: 'recorder' }], on: (name, fn) => listeners.set(name, fn), off: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); } };
        const store = { loadEnvelope: () => ({ ok: true, value: envelope }), getBranch: () => branch, restoreBranch };
        await bootstrap({ adapter, dependencies: { store, promptInjector: { refresh: vi.fn(async () => {}), clear: vi.fn() } } });

        await expect(Promise.resolve(listeners.get('swiped')(0))).resolves.toEqual({ ok: true });
        expect(restoreBranch).toHaveBeenCalledWith(message, 0);
    });

    it('keeps the default rollback swipe handler read-only when chat configOverrides disable it', async () => {
        const listeners = new Map(); const branch = { branchId: 'branch-0', baseSnapshot: { version: 0 }, baseStateVersion: 0, segments: [{ postSnapshot: { version: 1 } }] };
        const message = { swipe_id: 0, extra: { dualModelEngine: { messageId: 'm1', branch: structuredClone(branch) } }, swipe_info: [{ extra: { dualModelEngine: { messageId: 'm1', branch } } }] };
        const envelope = { stateVersion: 0, headRevision: 0, activeSnapshot: { version: 0 }, configOverrides: { enabled: false }, preset: { id: 'narrative' } };
        const restoreBranch = vi.fn(async () => ({ ok: true }));
        const adapter = { events: { MESSAGE_SWIPED: 'swiped', CHAT_CHANGED: 'chat', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', GENERATION_STOPPED: 'stopped' }, getContext: () => ({ chatId: 'chat-a', groupId: null, chat: [message], chatMetadata: { dualModelEngine: envelope } }), getSettings: () => ({ enabled: true }), listProfiles: () => [{ id: 'recorder' }], on: (name, fn) => listeners.set(name, fn), off: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); } };
        const store = { loadEnvelope: () => ({ ok: true, value: envelope }), getBranch: () => branch, restoreBranch };
        await bootstrap({ adapter, dependencies: { store, promptInjector: { refresh: vi.fn(async () => {}), clear: vi.fn() } } });

        await expect(Promise.resolve(listeners.get('swiped')(0))).resolves.toEqual({ ok: false, reason: 'read-only' });
        expect(restoreBranch).not.toHaveBeenCalled();
    });

    it('preserves bootstrap failure identity while unregistering the real partially registered tools and stopping listeners', async () => {
        const error = new Error('second registration'); const listeners = new Map(); const adapter = { events: { CHAT_CHANGED: 'chat', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', GENERATION_STOPPED: 'stopped' }, getContext: () => ({ chatId: 'a', groupId: null, chat: [], chatMetadata: { dualModelEngine: { stateVersion: 0, headRevision: 0, activeSnapshot: { version: 0 }, preset: { id: 'narrative' } } } }), getSettings: () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'narrative' }), listProfiles: () => [{ id: 'recorder' }], on: (name, fn) => listeners.set(name, fn), off: vi.fn((name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); }), registerTool: vi.fn(definition => { if (definition.name.endsWith('Damage')) throw error; }), unregisterTool: vi.fn(), saveChat: vi.fn() };
        await expect(bootstrap({ adapter, dependencies: { promptInjector: { refresh: vi.fn(), clear: vi.fn() } } })).rejects.toBe(error);
        expect(adapter.unregisterTool).toHaveBeenCalledWith('DualModelResolveD20Check'); expect(adapter.unregisterTool).not.toHaveBeenCalledWith('DualModelApplyD20Damage'); expect(adapter.off).toHaveBeenCalledTimes(4); expect(listeners).toEqual(new Map());
    });

    it.each(['bind', 'initialize'])('continues best-effort cleanup and preserves the %s failure object', async stage => {
        const error = new Error(stage); const listeners = new Map(); const registry = { register: vi.fn(), unregister: vi.fn(() => { throw new Error('unregister'); }) }; const rollbackManager = { bind: vi.fn(() => { if (stage === 'bind') throw error; }), destroy: vi.fn(() => { throw new Error('destroy'); }) };
        const store = { loadEnvelope: () => { if (stage === 'initialize') throw error; return { stateVersion: 0, headRevision: 0, activeSnapshot: { version: 0 }, preset: { id: 'narrative' } }; }, listRuleRecords: () => [] };
        const adapter = { events: { CHAT_CHANGED: 'chat', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', GENERATION_STOPPED: 'stopped' }, getContext: () => ({ chatId: 'a', groupId: null, chat: [], chatMetadata: { dualModelEngine: store.loadEnvelope() } }), getSettings: () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'narrative' }), listProfiles: () => [{ id: 'recorder' }], on: (name, fn) => listeners.set(name, fn), off: vi.fn((name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); }), registerTool: vi.fn(), saveChat: vi.fn() };
        await expect(bootstrap({ adapter, dependencies: { store, toolRegistry: registry, rollbackManager, promptInjector: { refresh: vi.fn(), clear: vi.fn() } } })).rejects.toBe(error);
        expect(registry.register).toHaveBeenCalledOnce(); expect(registry.unregister).toHaveBeenCalledOnce(); expect(rollbackManager.destroy).toHaveBeenCalledOnce(); expect(adapter.off).toHaveBeenCalledTimes(4); expect(listeners).toEqual(new Map());
    });

    it('retries only a failed tool unregistration while keeping bootstrap stop cleanup idempotent', async () => {
        const failure = new Error('damage unregister'); const registry = { register: vi.fn(), unregister: vi.fn().mockImplementationOnce(() => { throw failure; }) }; const rollbackManager = { bind: vi.fn(), destroy: vi.fn() }; const adapter = { events: { CHAT_CHANGED: 'chat', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', GENERATION_STOPPED: 'stopped' }, getContext: () => ({ chatId: 'a', groupId: null, chat: [], chatMetadata: {} }), getSettings: () => ({ enabled: false }), listProfiles: () => [], on: vi.fn(), off: vi.fn(), registerTool: vi.fn() };
        const result = await bootstrap({ adapter, dependencies: { toolRegistry: registry, rollbackManager, promptInjector: { refresh: vi.fn(), clear: vi.fn() } } });
        expect(() => result.orchestrator.stop()).toThrow(failure); expect(adapter.off).toHaveBeenCalledTimes(4); expect(rollbackManager.destroy).toHaveBeenCalledOnce();
        expect(() => result.orchestrator.stop()).not.toThrow(); expect(registry.unregister).toHaveBeenCalledTimes(2); expect(adapter.off).toHaveBeenCalledTimes(4); expect(rollbackManager.destroy).toHaveBeenCalledOnce();
    });
});
