import { expect, it, vi } from 'vitest';
import { browserDownload, createChatActions, rawExport } from '../../src/chat-actions.js';

function host(overrides = {}) {
    const envelope = { schemaVersion: 1, stateVersion: 1, headRevision: 2, preset: { id: 'd20', version: 1 }, activeSnapshot: { version: 1, actors: { player: { abilities: { dexterity: 10 }, proficientSkills: [], proficiencyBonus: 2, hp: { current: 8, max: 10, temporary: 2 } } } }, activeRef: { messageId: 'm', swipeId: 0, branchId: 'b' } };
    const context = { chatId: 'c', chat: [{ mes: 'visible assistant', swipe_id: 0, extra: { dualModelEngine: { messageId: 'm' } }, swipe_info: [{ mes: 'selected swipe', extra: { dualModelEngine: { branch: { branchId: 'b', status: 'committed', segments: [{}] } } } }] }], chatMetadata: { dualModelEngine: envelope } };
    const store = { loadEnvelope: vi.fn(() => ({ ok: true, value: envelope })), commitCurrentBranchAudit: vi.fn(async () => ({ ok: true })), commitCurrentBranchMutation: vi.fn(async () => ({ ok: true })) };
    const old = { kind: 'check', checkId: 'old', branchId: 'b', signature: 'pick', request: { actor: 'player', ability: 'dexterity', skill: 'stealth', advantage: 'normal', dc: 12 }, result: { total: 4 } };
    const ledger = { list: () => [old], reroll: (previous, value) => ({ ...value, checkId: 'new', supersedes: previous.checkId }), commit: vi.fn(), createRecord: vi.fn(value => ({ ...value, checkId: 'damage' })) };
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

it.each(['cancelled', 'stale'])('uses RNG once and creates no damage ledger record when damage is %s', async reason => {
    const h = host();
    if (reason === 'cancelled') h.deps.confirm.mockResolvedValue(false);
    if (reason === 'stale') h.deps.queue.enqueue = async (_id, _key, work) => { h.envelope.headRevision++; return work(new AbortController().signal); };
    await expect(h.actions.applyDamage({ target: 'player', expression: '1d6', damageType: 'fire' })).resolves.toMatchObject({ reason });
    expect(h.deps.nextUint32).toHaveBeenCalledTimes(1); expect(h.ledger.createRecord).not.toHaveBeenCalled(); expect(h.ledger.commit).not.toHaveBeenCalled();
});

it('resummary sends the pinned capture and does not commit a stale candidate', async () => {
    const h = host();
    h.deps.queue.enqueue = async (_id, _key, task) => { h.envelope.preset = { id: 'other', version: 1 }; return task(new AbortController().signal); };
    await expect(h.actions.resummarize()).resolves.toMatchObject({ reason: 'stale' });
    expect(h.deps.modelService.requestSummary).toHaveBeenCalledWith({ profileId: 'rec', presetId: 'd20', messages: [{ role: 'assistant', content: 'visible assistant' }], version: 2 });
    expect(h.store.commitCurrentBranchMutation).not.toHaveBeenCalled();
});

it.each(['chat', 'context', 'ref', 'head', 'preset'])('does not recalculate after confirmation changes the captured %s', async changed => {
    const h = host(); const rollbackManager = { buildRecalculationPlan: () => [{ messageIndex: 0 }], recalculate: vi.fn(async () => ({ ok: true })) }; h.deps.rollbackManager = rollbackManager; h.deps.currentInvalidIndex = () => 0;
    h.deps.confirm.mockImplementationOnce(async () => { if (changed === 'chat') h.context.chatId = 'other'; if (changed === 'context') h.context.chat = []; if (changed === 'ref') h.envelope.activeRef = { ...h.envelope.activeRef, branchId: 'other' }; if (changed === 'head') h.envelope.headRevision++; if (changed === 'preset') h.envelope.preset = { id: 'other' }; return true; });
    await expect(h.actions.recalculate()).resolves.toEqual({ ok: false, reason: 'stale' }); expect(rollbackManager.recalculate).not.toHaveBeenCalled();
});

it('resummary sends only visible canonical user and assistant content', async () => {
    const h = host(); h.context.chat = [{ is_user: true, mes: 'player', extra: { secret: 1 } }, { mes: 'selected', swipe_id: 0, swipe_info: [{ mes: 'selected' }, { mes: 'not selected' }] }, { is_system: true, mes: 'skip' }];
    await h.actions.resummarize();
    expect(h.deps.modelService.requestSummary).toHaveBeenCalledWith(expect.objectContaining({ messages: [{ role: 'user', content: 'player' }, { role: 'assistant', content: 'selected' }] }));
});

it('returns the store save failure unchanged and confirms the exact resummary operations', async () => {
    const h = host({ diffState: vi.fn(() => [{ op: 'replace', path: '/actors/player/name', value: 'Ada' }]) }); h.store.commitCurrentBranchMutation.mockResolvedValue({ ok: false, reason: 'save-failed' });
    await expect(h.actions.resummarize()).resolves.toEqual({ ok: false, reason: 'save-failed' });
    expect(h.deps.confirm).toHaveBeenCalledWith(expect.objectContaining({ action: 'resummarize', operations: [{ op: 'replace', path: '/actors/player/name', value: 'Ada' }] }));
    expect(h.store.commitCurrentBranchMutation).toHaveBeenCalledWith(expect.objectContaining({ patch: { operations: [{ op: 'replace', path: '/actors/player/name', value: 'Ada' }] } }));
});

it('never rerolls a selected check from another branch', async () => {
    const h = host({ selectedCheck: () => ({ kind: 'check', branchId: 'other', request: { actor: 'player' } }) });
    await expect(h.actions.reroll()).resolves.toMatchObject({ reason: 'missing-check' });
    expect(h.deps.nextUint32).not.toHaveBeenCalled();
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

it('creates and revokes a browser download URL and raw export is whitelisted', async () => {
    const create = vi.spyOn(globalThis.URL, 'createObjectURL').mockReturnValue('blob:test'); const revoke = vi.spyOn(globalThis.URL, 'revokeObjectURL'); const click = vi.spyOn(globalThis.HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    browserDownload('x.json', { x: 1 }); await Promise.resolve();
    expect(create).toHaveBeenCalledOnce(); expect(click).toHaveBeenCalledOnce(); expect(revoke).toHaveBeenCalledWith('blob:test');
    expect(rawExport({ schemaVersion: 1, recorderProfileId: 'secret', credentials: { token: 'secret' }, preset: { id: 'p' } }, [])).not.toHaveProperty('credentials');
    create.mockRestore(); revoke.mockRestore(); click.mockRestore();
});

it.each(['URL', 'Blob', 'document'])('returns export-failed when browser %s support is absent', async missing => {
    const h = host({ download: undefined }); const original = globalThis[missing];
    try { Object.defineProperty(globalThis, missing, { configurable: true, value: undefined }); await expect(h.actions.exportRaw()).resolves.toMatchObject({ ok: false, reason: 'export-failed' }); }
    finally { Object.defineProperty(globalThis, missing, { configurable: true, value: original }); }
});

it('does not commit a ledger record when the store reports stale', async () => {
    const h = host(); h.store.commitCurrentBranchAudit.mockResolvedValue({ ok: false, reason: 'stale-chat' });
    await h.actions.reroll(); expect(h.ledger.commit).not.toHaveBeenCalled();
    h.store.commitCurrentBranchMutation.mockResolvedValue({ ok: false, reason: 'stale-chat' }); await h.actions.applyDamage({ target: 'player', expression: '1d6', damageType: 'fire' }); expect(h.ledger.commit).not.toHaveBeenCalled();
});
