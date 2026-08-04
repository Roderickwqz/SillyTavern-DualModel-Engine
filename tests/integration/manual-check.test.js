import { expect, it, vi } from 'vitest';
import { bootstrap } from '../../src/index.js';
import { NAMESPACE } from '../../src/constants.js';
import { createChatTaskQueue } from '../../src/task-queue.js';
import { d20TestState } from '../fixtures/d20.js';
import { createCustomRuleAdapter } from '../../src/rules/custom.js';

const check = { actor: 'player', action: 'Pick the lock', ability: 'dexterity', skill: 'sleight_of_hand', dc: 12, advantage: 'normal', reason: 'locked door' };
const damage = { target: 'player', expression: '1d6', damageType: 'fire', reason: 'trap' };
const recorderPatch = { base_version: 0, operations: [{ op: 'add', path: '/inventory/-', value: 'recorded', reason: 'recorder' }] };

function host() {
    const user = { is_user: true, mes: 'I pick the lock.', extra: { [NAMESPACE]: { messageId: 'u1' } } };
    const branch = { branchId: 'branch-1', baseStateVersion: 0, baseSnapshot: d20TestState(), status: 'committed', segments: [{ requestId: 'old', userMessageId: 'u1', assistantTextHash: 'old', checks: [], patch: recorderPatch, postSnapshot: d20TestState() }] };
    const assistant = {
        is_user: false, mes: 'The old reply.', swipe_id: 0,
        extra: { [NAMESPACE]: { messageId: 'a1' } },
        swipe_info: [{ extra: { [NAMESPACE]: { messageId: 'a1', branch } } }],
    };
    const context = { chatId: 'chat-a', groupId: null, chat: [user, assistant], chatMetadata: { [NAMESPACE]: { schemaVersion: 1, stateVersion: 0, headRevision: 0, activeSnapshot: d20TestState(), activeRef: { messageId: 'a1', swipeId: 0, branchId: 'branch-1' }, taskStatus: { state: 'idle', requestId: null }, lastCommittedRequestId: 'old', preset: { id: 'd20-lite' } } } };
    const listeners = new Map(); const events = []; let id = 0;
    const adapter = { events: { CHAT_CHANGED: 'chat-changed', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', GENERATION_STOPPED: 'stopped' }, getContext: () => context, getSettings: () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'd20-lite', adjudication: 'manual', injectionBudget: 100 }), listProfiles: () => [{ id: 'recorder' }], on: (name, fn) => listeners.set(name, fn), off: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); }, registerTool: vi.fn(), unregisterTool: vi.fn(), saveChat: vi.fn(async () => { events.push('save'); }) };
    const promptInjector = { refresh: vi.fn(async () => {}), clear: vi.fn() };
    const modelService = { requestPatch: vi.fn(async () => ({ patch: recorderPatch })) };
    return { adapter, context, events, promptInjector, modelService, addAssistant(text = 'New reply.') { const message = { is_user: false, mes: text, swipe_id: 0, extra: {}, swipe_info: [{ extra: {} }] }; context.chat.push(message); return message; }, makeId: () => `id-${++id}` };
}

async function appFor(subject, extra = {}) {
    const queue = createChatTaskQueue();
    const app = await bootstrap({ adapter: subject.adapter, dependencies: { modelService: subject.modelService, promptInjector: subject.promptInjector, queue, makeId: subject.makeId, nextUint32: vi.fn(() => 14), ...extra } });
    const commit = app.ledger.commit.bind(app.ledger); app.ledger.commit = records => { subject.events.push('ledger'); return commit(records); };
    return { app, queue };
}

it('rejects manual checks during an active generation without RNG, audit, save, or ledger side effects', async () => {
    const subject = host(); const nextUint32 = vi.fn(() => 14); const { app, queue } = await appFor(subject, { nextUint32 });
    await app.orchestrator.beforeGeneration('normal');
    await expect(app.adjudicator.resolveManual(check)).rejects.toThrow('Finish generation');
    expect(nextUint32).not.toHaveBeenCalled(); expect(subject.adapter.saveChat).not.toHaveBeenCalled(); expect(app.ledger.list()).toEqual([]);
    subject.addAssistant(); await app.orchestrator.afterGeneration(); await queue.waitForIdle('chat-a');
    expect(subject.context.chat.at(-1).swipe_info[0].extra[NAMESPACE].branch.status).toBe('committed'); expect(subject.context.chatMetadata[NAMESPACE].activeRef.messageId).not.toBe('a1');
    app.orchestrator.stop();
});

it('waits behind Recorder then audits the newly committed active branch before publishing its ledger record', async () => {
    const subject = host(); let release; subject.modelService.requestPatch.mockImplementation(() => new Promise(resolve => { release = () => resolve({ patch: recorderPatch }); })); const { app } = await appFor(subject);
    await app.orchestrator.beforeGeneration('normal'); subject.addAssistant(); await app.orchestrator.afterGeneration(); await vi.waitFor(() => expect(release).toBeTypeOf('function'));
    const manual = app.adjudicator.resolveManual(check); await Promise.resolve();
    expect(subject.adapter.saveChat).not.toHaveBeenCalled(); expect(app.ledger.list()).toEqual([]);
    release(); await manual;
    const branch = subject.context.chat.at(-1).swipe_info[0].extra[NAMESPACE].branch;
    expect(branch.status).toBe('committed'); expect(branch.segments).toHaveLength(1); expect(branch.segments[0].checks).toHaveLength(1);
    expect(subject.context.chatMetadata[NAMESPACE].activeRef).toMatchObject({ messageId: subject.context.chat.at(-1).extra[NAMESPACE].messageId, branchId: branch.branchId });
    expect(subject.events).toEqual(['save', 'ledger', 'save', 'ledger']); expect(app.ledger.list()).toEqual(branch.segments[0].checks);
    app.orchestrator.stop();
});

it('cancels a queued manual audit without leaving a record behind', async () => {
    const subject = host(); let release; subject.modelService.requestPatch.mockImplementation(() => new Promise(resolve => { release = () => resolve({ patch: recorderPatch }); })); const { app } = await appFor(subject);
    await app.orchestrator.beforeGeneration('normal'); subject.addAssistant(); await app.orchestrator.afterGeneration(); await vi.waitFor(() => expect(release).toBeTypeOf('function'));
    const manual = app.adjudicator.resolveManual(check); app.orchestrator.stop(); release(); await expect(manual).rejects.toMatchObject({ name: 'AbortError' });
    expect(app.ledger.list()).toEqual([]); expect(subject.context.chat.at(-1).swipe_info[0].extra[NAMESPACE].branch?.segments[0].checks ?? []).toEqual([]);
});

it('authorizes manual checks for the active mapped custom D20 preset', async () => {
    const subject = host(); const mappedState = { version: 0, sheet: { members: { player: { stats: { dexterity: 14 }, proficiency: 2, skills: ['sleight_of_hand'], vitals: { current: 10, max: 10, temporary: 0 }, effects: [] } } } };
    const preset = createCustomRuleAdapter({ id: 'custom-d20', name: 'Custom', presetVersion: 1, stateSchema: {}, initialState: mappedState, allowedPaths: ['/sheet'], lockedPaths: ['/version'], injection: [], ui: [], d20: { actorsPath: '/sheet/members', abilitiesPath: '/stats', proficiencyBonusPath: '/proficiency', proficientSkillsPath: '/skills', hpPath: '/vitals', conditionsPath: '/effects', skillAbilities: { sleight_of_hand: 'dexterity' }, naturalRollPolicy: 'critical' } });
    const envelope = subject.context.chatMetadata[NAMESPACE]; envelope.preset = { id: 'custom-d20', version: 1 }; envelope.activeSnapshot = mappedState;
    subject.adapter.getSettings = () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'custom-d20', adjudication: 'manual', injectionBudget: 100 });
    const { app } = await appFor(subject, { presetManager: { getPreset: id => id === 'custom-d20' ? preset : undefined } });
    await expect(app.adjudicator.resolveManual(check)).resolves.toMatchObject({ kind: 'check', result: { total: 19 } });
    app.orchestrator.stop();
});

it('audits mapped custom D20 damage through preset actor accessors', async () => {
    const subject = host(); const definitions = new Map(); const mappedState = { version: 0, sheet: { members: { player: { stats: { dexterity: 14 }, proficiency: 2, skills: ['sleight_of_hand'], vitals: { current: 10, max: 10, temporary: 0 }, effects: [] } } } };
    const preset = createCustomRuleAdapter({ id: 'custom-d20', name: 'Custom', presetVersion: 1, stateSchema: {}, initialState: mappedState, allowedPaths: ['/sheet'], lockedPaths: ['/version'], injection: [], ui: [], d20: { actorsPath: '/sheet/members', abilitiesPath: '/stats', proficiencyBonusPath: '/proficiency', proficientSkillsPath: '/skills', hpPath: '/vitals', conditionsPath: '/effects', skillAbilities: { sleight_of_hand: 'dexterity' } } });
    const envelope = subject.context.chatMetadata[NAMESPACE]; envelope.preset = { id: 'custom-d20', version: 1 }; envelope.activeSnapshot = mappedState;
    subject.adapter.getSettings = () => ({ enabled: true, recorderProfileId: 'recorder', rulePresetId: 'custom-d20', adjudication: 'automatic-tool', injectionBudget: 100 }); subject.adapter.registerTool = definition => definitions.set(definition.name, definition); subject.adapter.unregisterTool = name => definitions.delete(name);
    const { app } = await appFor(subject, { presetManager: { getPreset: id => id === 'custom-d20' ? preset : undefined }, adjudicator: { resolveBeforeGeneration: vi.fn(async () => ({ injectedText: '' })) } });
    await app.orchestrator.beforeGeneration('normal');
    await expect(definitions.get('DualModelApplyD20Damage').action(damage)).resolves.toMatchObject({ kind: 'damage', result: { hpBefore: 10, hpAfter: 7 } });
    app.orchestrator.stop();
});
