import { expect, it, vi } from 'vitest';
import { bootstrap } from '../../src/index.js';
import { createSTAdapter, createStrictChatSaver } from '../../src/st-adapter.js';
import { createStateStore } from '../../src/state-store.js';
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

it('rolls back state-store memory when strict SillyTavern persistence rejects a save', async () => {
    const message = { extra: { dualModelEngine: { messageId: 'm1' } }, swipe_id: 0, swipe_info: [{ extra: { dualModelEngine: { messageId: 'm1' } } }] };
    const context = {
        groupId: null, characterId: 0, chatId: 'chat.jsonl', characters: [{ name: 'Ava', avatar: 'ava.png' }], chat: [message],
        chatMetadata: { dualModelEngine: { schemaVersion: 1, stateVersion: 0, headRevision: 0, activeSnapshot: { version: 0 }, activeRef: null, taskStatus: { state: 'idle', requestId: null }, lastCommittedRequestId: null } },
    };
    const fetch = vi.fn().mockResolvedValue({ ok: false, statusText: 'Conflict' });
    const adapter = createSTAdapter({ getContext: () => context, strictSaveChat: createStrictChatSaver({ getContext: () => context, getRequestHeaders: () => ({}), compressRequest: async request => request, fetch }) });
    const store = createStateStore({ adapter, makeId: () => 'generated', hashText: async text => `hash:${text}` });
    const before = structuredClone(context);

    await expect(store.commitSegment({ chatId: 'chat.jsonl', message, messageId: 'm1', branchId: 'b1', swipeId: 0, expectedHeadRevision: 0, baseStateVersion: 0, requestId: 'r1', userMessageId: 'u1', baseSnapshot: { version: 0 }, patch: { operations: [] }, checks: [], assistantText: 'answer', nextState: { version: 1 }, isContinue: false })).resolves.toMatchObject({ ok: false, reason: 'save-failed', error: expect.any(Error) });

    expect(context).toEqual(before);
    expect(fetch).toHaveBeenCalledOnce();
});
