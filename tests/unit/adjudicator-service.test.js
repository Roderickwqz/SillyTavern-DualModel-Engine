import { expect, it, vi } from 'vitest';
import { createAdjudicatorService } from '../../src/adjudicator-service.js';

const request = { required: true, actor: 'player', action: 'jump', ability: 'dexterity', skill: 'acrobatics', dc: 12, advantage: 'normal' };
function deps(overrides = {}) { return { toolProbe: { supported: false }, requestDecision: vi.fn(async () => request), validateInput: vi.fn(() => ({ ok: true, errors: [] })), stageCheck: vi.fn(async value => ({ checkId: 'c1', request: value, result: { total: 15, dc: 12, outcome: 'success' } })), formatCheck: check => `Formal check ${check.checkId}`, confirm: vi.fn(async () => true), resolveManualCheck: vi.fn(async () => ({ checkId: 'manual' })), ...overrides }; }

it('downgrades unsupported automatic tools to an authoritative preflight', async () => {
    const d = deps(); const result = await createAdjudicatorService(d).resolveBeforeGeneration({ strategy: 'automatic-tool', playerText: 'jump' });
    expect(result).toMatchObject({ strategy: 'enforced-preflight', required: true, check: { checkId: 'c1' } }); expect(d.stageCheck).toHaveBeenCalledWith(request, expect.any(Object));
});
it('honours supported automatic, confirmation cancellation, manual, validation and resolver errors', async () => {
    await expect(createAdjudicatorService(deps({ toolProbe: { supported: true } })).resolveBeforeGeneration({ strategy: 'automatic-tool' })).resolves.toMatchObject({ strategy: 'automatic-tool', required: false });
    await expect(createAdjudicatorService(deps({ confirm: vi.fn(async () => false) })).resolveBeforeGeneration({ strategy: 'confirm' })).resolves.toMatchObject({ cancelled: true, required: false });
    const manual = deps(); await expect(createAdjudicatorService(manual).resolveBeforeGeneration({ strategy: 'manual' })).resolves.toMatchObject({ strategy: 'manual', required: false }); expect(manual.requestDecision).not.toHaveBeenCalled();
    await expect(createAdjudicatorService(deps({ validateInput: () => ({ ok: false, errors: ['bad'] }) })).resolveBeforeGeneration({ strategy: 'enforced-preflight' })).rejects.toThrow('bad');
    await expect(createAdjudicatorService(deps({ stageCheck: async () => { throw new Error('rule failed'); } })).resolveBeforeGeneration({ strategy: 'enforced-preflight' })).rejects.toThrow('rule failed');
});
