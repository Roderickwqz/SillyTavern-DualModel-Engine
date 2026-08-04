import { expect, it, vi } from 'vitest';
import { createStateTab } from '../../src/ui/state-tab.js';

it('previews a Patch and requires confirmation for locked fields', async () => {
    const confirm = vi.fn().mockResolvedValue(false);
    const tab = createStateTab({ validateState: () => ({ ok: true, errors: [] }), diffState: () => [{ op: 'replace', path: '/actors/player/hp/current', value: 9 }], confirm, commitManualPatch: vi.fn() });
    await expect(tab.saveStateEdit({ version: 1 }, { version: 1 }, ['/actors'])).resolves.toEqual({ ok: false, reason: 'cancelled' });
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ lockedPaths: ['/actors'] }));
});

it('rejects version changes and invalid JSON without committing', async () => {
    const commitManualPatch = vi.fn();
    const tab = createStateTab({ validateState: () => ({ ok: false, errors: [{ message: 'invalid' }] }), diffState: vi.fn(), confirm: vi.fn(), commitManualPatch });
    await expect(tab.saveStateEdit({ version: 1 }, { version: 'bad' }, [])).resolves.toMatchObject({ ok: false, reason: 'system-locked-version' });
    await expect(tab.saveStateEdit({ version: 1 }, { version: 1 }, [])).resolves.toMatchObject({ ok: false, reason: 'invalid-state' });
    expect(commitManualPatch).not.toHaveBeenCalled();
});
