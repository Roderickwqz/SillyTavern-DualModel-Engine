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
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));

    const normal = await host.runNarrativeTurn('I take the key.', 'You take the key.');
    expect(normal.ended).toEqual({ ok: true, queued: true });
    expect(await host.bindD20()).toEqual({ ok: true });
    const check = await host.runFormalCheck({ actor: 'player', action: 'open door', ability: 'dexterity', skill: 'sleight_of_hand', dc: 12, advantage: 'normal', reason: 'locked door' });
    expect(check.record).toMatchObject({ kind: 'check', result: { total: 24, outcome: 'critical-success' } });
    expect(check.envelope.activeRef).toBeTruthy();
    const continued = await host.runContinue(' It swings inward.');
    expect(continued).toMatchObject({ ok: true });
    await host.importAndBindPreset(readFileSync('tests/fixtures/custom-preset.json', 'utf8'));
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

it('downgrades group chats without starting a Recorder task', async () => {
    const host = createAcceptanceHost();
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    host.setGroup();
    await expect(app.orchestrator.beforeGeneration('normal')).resolves.toMatchObject({ ignored: true, reason: 'group-chat' });
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
