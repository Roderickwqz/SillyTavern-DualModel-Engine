import { expect, it, vi } from 'vitest';
import { bootstrap } from '../../src/index.js';
import { createAcceptanceHost } from '../fixtures/fake-host.js';

it('keeps the canonical plugin persistence projection byte-identical when a Recorder save fails', async () => {
    let release;
    const host = createAcceptanceHost({ requestPatch: input => new Promise(resolve => { release = () => resolve({ patch: { base_version: input.baseVersion, operations: [] } }); }) });
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    await host.beginTurn('act'); await host.endTurn('result');
    await vi.waitFor(() => expect(host.recorderRequests()).toHaveLength(1));
    const before = host.snapshotPluginData(); host.failAllSaves();

    release(); await host.waitFor('acceptance-chat'); await Promise.resolve(); await Promise.resolve();

    expect(host.snapshotPluginData()).toBe(before);
    host.failAllSaves(false);
    await app.stop();
});

it('does not mutate the current branch when an explicit reroll save fails', async () => {
    const host = createAcceptanceHost();
    const app = host.attach(await bootstrap({ adapter: host.adapter, dependencies: host.dependencies }));
    await host.bindD20();
    await host.runFormalCheck({ actor: 'player', action: 'open door', ability: 'dexterity', skill: 'sleight_of_hand', dc: 12, advantage: 'normal', reason: 'locked door' });
    const before = host.snapshotPluginData();
    host.failAllSaves();

    await host.explicitReroll();

    expect(host.snapshotPluginData()).toBe(before);
    host.failAllSaves(false);
    await app.stop();
});
