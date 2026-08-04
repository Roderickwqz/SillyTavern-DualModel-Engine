import { expect, it, vi } from 'vitest';
import { createChatActions } from '../../src/chat-actions.js';

function host(overrides = {}) {
    const envelope = { schemaVersion: 1, stateVersion: 1, headRevision: 2, preset: { id: 'd20', version: 1 }, activeSnapshot: { version: 1, actors: { player: { abilities: { dexterity: 10 }, proficientSkills: [], proficiencyBonus: 2, hp: { current: 8, max: 10, temporary: 2 } } } }, activeRef: { messageId: 'm', swipeId: 0, branchId: 'b' } };
    const context = { chatId: 'c', chat: [{ mes: 'visible assistant', swipe_id: 0, extra: { dualModelEngine: { messageId: 'm' } }, swipe_info: [{ mes: 'selected swipe', extra: { dualModelEngine: { branch: { branchId: 'b', status: 'committed', segments: [{}] } } } }] }], chatMetadata: { dualModelEngine: envelope } };
    const store = { loadEnvelope: vi.fn(() => ({ ok: true, value: envelope })), commitCurrentBranchAudit: vi.fn(async () => ({ ok: true })), commitCurrentBranchMutation: vi.fn(async () => ({ ok: true })) };
    const old = { kind: 'check', checkId: 'old', branchId: 'b', signature: 'pick', request: { actor: 'player', ability: 'dexterity', skill: 'stealth', advantage: 'normal', dc: 12 }, result: { total: 4 } };
    const ledger = { list: () => [old], reroll: (previous, value) => ({ ...value, checkId: 'new', supersedes: previous.checkId }), commit: vi.fn(), createRecord: value => ({ ...value, checkId: 'damage' }) };
    const preset = { readActor: (state, id) => state.actors[id], writeActor: (state, id, actor) => { state.actors[id] = actor; }, skillAbilities: { stealth: 'dexterity' }, naturalRollPolicy: 'normal' };
    const deps = { adapter: { getContext: () => context }, store, ledger, queue: { enqueue: (_id, _key, task) => task(new AbortController().signal) }, orchestrator: { getActiveGeneration: () => null }, config: () => ({ enabled: true, recorderProfileId: 'rec' }), makeId: () => 'x', nextUint32: vi.fn(() => 1), preset: () => preset, validateState: vi.fn(() => ({ ok: true, errors: [] })), validateDamage: vi.fn(() => ({ ok: true, errors: [] })), modelService: { requestSummary: vi.fn(async () => ({ state: { version: 2, actors: {} } })) }, confirm: vi.fn(async () => true), download: vi.fn(), ...overrides };
    return { context, envelope, store, ledger, deps, actions: createChatActions(deps) };
}

it('reroll keeps old immutable, commits store before ledger, and records supersedes', async () => {
    const h = host(); const old = h.ledger.list()[0];
    await expect(h.actions.reroll()).resolves.toMatchObject({ ok: true });
    expect(old).toEqual(expect.objectContaining({ checkId: 'old' }));
    expect(h.store.commitCurrentBranchAudit).toHaveBeenCalledWith(expect.objectContaining({ record: expect.objectContaining({ supersedes: 'old' }) }));
    expect(h.ledger.commit).toHaveBeenCalledWith([expect.objectContaining({ checkId: 'new' })]);
    expect(h.deps.nextUint32).toHaveBeenCalled();
});

it.each(['save failure', 'head change', 'chat change', 'envelope change', 'base change', 'ref change', 'swipe conflict'])('reroll never commits ledger on %s', async kind => {
    const h = host();
    if (kind === 'save failure') h.store.commitCurrentBranchAudit.mockResolvedValue({ ok: false, reason: 'save-failed' });
    if (kind === 'head change') h.deps.queue.enqueue = async (_id, _key, task) => { h.envelope.headRevision++; return task(new AbortController().signal); };
    if (kind === 'chat change') h.deps.queue.enqueue = async (_id, _key, task) => { h.context.chat = []; return task(new AbortController().signal); };
    if (kind === 'envelope change') h.deps.queue.enqueue = async (_id, _key, task) => { const changed = { ...h.envelope }; h.context.chatMetadata.dualModelEngine = changed; h.store.loadEnvelope.mockReturnValue({ ok: true, value: changed }); return task(new AbortController().signal); };
    if (kind === 'base change') h.deps.queue.enqueue = async (_id, _key, task) => { h.envelope.stateVersion++; return task(new AbortController().signal); };
    if (kind === 'ref change' || kind === 'swipe conflict') h.deps.queue.enqueue = async (_id, _key, task) => { h.envelope.activeRef = { ...h.envelope.activeRef, swipeId: 1 }; return task(new AbortController().signal); };
    await h.actions.reroll(); expect(h.ledger.commit).not.toHaveBeenCalled();
});

it('rejects invalid manual damage before RNG and commits HP and record together', async () => {
    const h = host(); h.deps.validateDamage.mockReturnValue({ ok: false, errors: [{ message: 'bad' }] });
    await expect(h.actions.applyDamage({})).resolves.toMatchObject({ reason: 'invalid-damage' }); expect(h.deps.nextUint32).not.toHaveBeenCalled();
    h.deps.validateDamage.mockReturnValue({ ok: true, errors: [] });
    await h.actions.applyDamage({ target: 'player', expression: '1d6', type: 'fire', reason: 'test' });
    expect(h.store.commitCurrentBranchMutation).toHaveBeenCalledWith(expect.objectContaining({ source: 'manual-damage', record: expect.objectContaining({ kind: 'damage' }), nextState: expect.objectContaining({ version: 2 }) }));
});

it('stages damage once, confirms its HP preview, then commits that exact result', async () => {
    const h = host();
    await h.actions.applyDamage({ target: 'player', expression: '1d6', damageType: 'fire', reason: 'test' });
    expect(h.deps.confirm).toHaveBeenCalledWith(expect.objectContaining({ action: 'apply-damage', preview: expect.objectContaining({ hpBefore: 8, hpAfter: 8 }) }));
    expect(h.deps.nextUint32).toHaveBeenCalledTimes(1);
    expect(h.store.commitCurrentBranchMutation).toHaveBeenCalledWith(expect.objectContaining({ nextState: expect.objectContaining({ actors: expect.objectContaining({ player: expect.objectContaining({ hp: expect.objectContaining({ current: 8, temporary: 0 }) }) }) }) }));
});

it('resummary sends the pinned capture and does not commit a stale candidate', async () => {
    const h = host();
    h.deps.queue.enqueue = async (_id, _key, task) => { h.envelope.preset = { id: 'other', version: 1 }; return task(new AbortController().signal); };
    await expect(h.actions.resummarize()).resolves.toMatchObject({ reason: 'stale' });
    expect(h.deps.modelService.requestSummary).toHaveBeenCalledWith({ profileId: 'rec', presetId: 'd20', messages: h.context.chat, version: 2 });
    expect(h.store.commitCurrentBranchMutation).not.toHaveBeenCalled();
});

it('imports asynchronously without changing settings on rejected or oversized files', async () => {
    const h = host({ pickFile: vi.fn(async () => ({ text: async () => { throw new Error('read failed'); } })) });
    await expect(h.actions.importPreset()).resolves.toMatchObject({ ok: false, reason: 'invalid-preset' });
});

it('exports custom JSON once and redacts raw credentials', async () => {
    const h = host({ presetManager: { exportPreset: vi.fn(() => '{"id":"custom"}') } });
    h.envelope.credentials = { token: 'secret' }; h.envelope.recorderProfileId = 'rec';
    await h.actions.exportPreset('custom'); await h.actions.exportRaw();
    expect(h.deps.download).toHaveBeenNthCalledWith(1, 'dualmodel-preset-custom.json', '{"id":"custom"}');
    expect(h.deps.download.mock.calls[1][1]).not.toHaveProperty('credentials');
    expect(h.deps.download.mock.calls[1][1]).not.toHaveProperty('recorderProfileId');
});
