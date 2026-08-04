import { expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { bootstrap } from '../../src/index.js';
import { createAcceptanceHost } from '../fixtures/fake-host.js';

const longChat = JSON.parse(readFileSync('tests/fixtures/long-chat.json', 'utf8'));

it('exposes one idempotent public stop lifecycle that disposes the queue', async () => {
    const context = { chatId: 'acceptance', groupId: null, chat: [], chatMetadata: {} };
    const queue = { enqueue: vi.fn(), waitForIdle: vi.fn(async () => {}), cancelChat: vi.fn(), getStatus: () => ({ state: 'idle', requestId: null }), dispose: vi.fn() };
    const adapter = { events: { CHAT_CHANGED: 'chat', GENERATION_AFTER_COMMANDS: 'before', GENERATION_ENDED: 'ended', GENERATION_STOPPED: 'stopped' }, getContext: () => context, getSettings: () => ({ enabled: false }), listProfiles: () => [], on: vi.fn(), off: vi.fn(), saveChat: vi.fn() };
    const app = await bootstrap({ adapter, dependencies: { queue, promptInjector: { refresh: vi.fn(), clear: vi.fn() } } });

    await app.stop();
    await app.stop();

    expect(queue.dispose).toHaveBeenCalledOnce();
    expect(adapter.off).toHaveBeenCalledTimes(5);
});

it('keeps normal narrative, formal D20, and custom-preset state across reload', async () => {
    const host = createAcceptanceHost();
    host.dependencies.pickPresetFile = async () => ({ text: async () => readFileSync('tests/fixtures/custom-preset.json', 'utf8') });
    host.dependencies.showConfirm = async () => true;
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));

    const normal = await host.runNarrativeTurn('I take the key.', 'You take the key.');
    expect(normal.ended).toEqual({ ok: true, queued: true });
    expect(await host.bindD20()).toEqual({ ok: true });
    const check = await host.runFormalCheck({ actor: 'player', action: 'open door', ability: 'dexterity', skill: 'sleight_of_hand', dc: 12, advantage: 'normal', reason: 'locked door' });
    expect(check.record).toMatchObject({ kind: 'check', result: { total: 24, outcome: 'critical-success' } });
    expect(check.envelope.activeRef).toBeTruthy();
    const continued = await host.runContinue(' It swings inward.');
    expect(continued).toMatchObject({ ok: true });
    await host.importAndBindPreset();
    expect(host.currentPresetId()).toBe('relationship-meter');
    const beforeReload = host.snapshotPluginData();

    await app.stop();
    const reloaded = host.attach(await bootstrap({ adapter: host.reloadAdapter(), dependencies: host.dependencies }));
    expect(host.snapshotPluginData()).toBe(beforeReload);
    expect(reloaded.capabilities.supported).toBe(true);
    await reloaded.stop();
});

it('reuses the ordinary swipe check and records explicit reroll lineage through public actions', async () => {
    const host = createAcceptanceHost();
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    await host.bindD20();
    const request = { actor: 'player', action: 'open door', ability: 'dexterity', skill: 'sleight_of_hand', dc: 12, advantage: 'normal', reason: 'locked door' };
    const first = await host.runFormalCheck(request);
    const reused = await host.createSwipeAndRunSameCheck(request);
    expect(reused.record.checkId).toBe(first.record.checkId);
    await host.restoreSelectedSwipe();
    const rerolled = await host.explicitReroll();
    expect(rerolled.record).toMatchObject({ supersedes: first.record.checkId });
    expect(rerolled.record.checkId).not.toBe(first.record.checkId);
    await app.stop();
});

it('recovers state after a selected message is deleted', async () => {
    const host = createAcceptanceHost();
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    expect(await host.runNarrativeTurn('first', 'first result')).toMatchObject({ ok: true });
    const before = host.currentState();
    expect(await host.runNarrativeTurn('second', 'second result')).toMatchObject({ ok: true });
    const recovered = await host.deleteLastMessage();
    expect(recovered).toMatchObject({ ok: true });
    expect(host.currentState()).toEqual(before);
    await app.stop();
});

it('invalidates and recalculates through the bound edit event', async () => {
    const host = createAcceptanceHost();
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    await host.runNarrativeTurn('first', 'first result');
    await host.runNarrativeTurn('second', 'second result');

    await expect(host.editLastMessage('corrected second result')).resolves.toMatchObject({ ok: true });
    expect(host.recorderRequests().at(-1).assistantText).toBe('corrected second result');
    expect(host.currentState().version).toBe(2);
    await app.stop();
});

it('waits for the prior Recorder task before capturing the next turn', async () => {
    let release;
    const host = createAcceptanceHost({ requestPatch: input => new Promise(resolve => { release = () => resolve({ patch: { base_version: input.baseVersion, operations: [] } }); }) });
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    await host.beginTurn('first'); await host.endTurn('first answer');
    await vi.waitFor(() => expect(host.recorderRequests()).toHaveLength(1));
    const next = host.beginTurn('second');
    await Promise.resolve();
    expect(host.recorderRequests()).toHaveLength(1);
    release(); await host.waitFor('acceptance-chat');
    await expect(next).resolves.toMatchObject({ ok: true });
    await app.stop();
});

it('repairs an invalid Recorder response through the bootstrap model service', async () => {
    let attempts = 0;
    const host = createAcceptanceHost({ requestProfile: async () => ({ content: ++attempts === 1 ? 'not json' : JSON.stringify({ base_version: 0, operations: [] }) }) });
    host.adapter.getSettings().adjudication = 'manual';
    host.dependencies.modelService = undefined;
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));

    await expect(host.runNarrativeTurn('repair this', 'repaired answer')).resolves.toMatchObject({ ok: true });
    expect(attempts).toBe(2);
    expect(host.currentState().version).toBe(1);
    await app.stop();
});

it.each([
    ['chat', async host => host.switchChatNow('other-chat'), 'chat-changed'],
    ['swipe', async host => { host.adapter.getContext().chat.at(-1).swipe_id = 0; }, 'stale-swipe'],
])('discards a stale %s Recorder result without changing canonical data', async (_kind, stale, reason) => {
    let release; let calls = 0;
    const host = createAcceptanceHost({ requestPatch: input => {
        const value = { patch: { base_version: input.baseVersion, operations: [{ op: 'add', path: '/inventory/-', value: 'late', reason: 'late result' }] } };
        if (_kind === 'swipe' && ++calls === 1) return value;
        return new Promise(resolve => { release = () => resolve(value); });
    } });
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    if (_kind === 'swipe') {
        await host.runNarrativeTurn('seed', 'seed answer');
        const message = host.adapter.getContext().chat.at(-1); message.swipe_info.push({ extra: {} }); await host.switchSwipeNow(1);
        await app.orchestrator.beforeGeneration('swipe'); message.mes = 'swipe answer'; await app.orchestrator.afterGeneration();
    } else { await host.beginTurn('late request'); await host.endTurn('late answer'); }
    await vi.waitFor(() => expect(host.recorderRequests().length).toBeGreaterThan(_kind === 'swipe' ? 1 : 0));
    const before = host.snapshotPluginData();
    await stale(host); release(); await host.waitFor('acceptance-chat');
    expect(host.snapshotPluginData()).toBe(before);
    expect(host.adapter.getSettings().diagnostics).toContainEqual(expect.objectContaining({ reason }));
    await app.stop();
});

it('downgrades group chats without starting a Recorder task', async () => {
    const host = createAcceptanceHost();
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    host.setGroup();
    await expect(app.orchestrator.beforeGeneration('normal')).resolves.toMatchObject({ ignored: true, reason: 'group-chat' });
    await app.stop();
});

it('downgrades unsupported automatic tools to enforced preflight and shows the probe reason', async () => {
    const host = createAcceptanceHost();
    const decide = vi.fn(async () => ({ decision: { required: false } }));
    host.dependencies.runToolProbe = vi.fn(async () => ({ supported: false, reason: 'probe invocation failed' }));
    host.dependencies.modelService.requestDecision = decide;
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    let settingsHost = document.querySelector('#extensions_settings');
    if (!settingsHost) { settingsHost = document.createElement('div'); settingsHost.id = 'extensions_settings'; document.body.append(settingsHost); }
    await app.ui.mount(); await app.ui.render();
    settingsHost.querySelector('[data-dme-action="probe-tools"]').click();
    await Promise.resolve(); await Promise.resolve(); await app.ui.render();

    expect(host.dependencies.runToolProbe).toHaveBeenCalledOnce();
    expect(host.adapter.getSettings().toolProbe).toMatchObject({ supported: false, reason: 'probe invocation failed' });
    await host.bindD20();
    await host.runNarrativeTurn('I examine the lock.', 'The lock is old.');

    expect(decide).toHaveBeenCalledOnce();
    expect(settingsHost.querySelector('[data-dme-role="diagnostic-reasons"]').textContent).toContain('Tool calling unavailable: probe invocation failed');
    await app.stop();
});

it('processes a turn after a long persisted chat fixture without mutating prior messages', async () => {
    const host = createAcceptanceHost();
    host.seedChat(longChat);
    const before = structuredClone(longChat);
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));

    await expect(host.runNarrativeTurn('I map the eastern stair.', 'The stair descends into darkness.')).resolves.toMatchObject({ ok: true });

    expect(host.adapter.getContext().chat.slice(0, before.length)).toEqual(before);
    expect(host.recorderRequests().at(-1)).toMatchObject({ playerText: 'I map the eastern stair.', assistantText: 'The stair descends into darkness.', baseVersion: 0 });
    await app.stop();
});
