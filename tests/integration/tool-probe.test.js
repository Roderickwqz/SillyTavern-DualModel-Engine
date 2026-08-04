import { expect, it, vi } from 'vitest';
import { runDynamicToolProbe } from '../../src/capability-probe.js';

function host({ invoke = true, error = null } = {}) { const chat = [{ is_user: true, mes: 'existing' }]; const adapter = { registerTool: vi.fn(), unregisterTool: vi.fn(), probeMainTool: vi.fn(async ({ definition }) => { if (error) return { supported: true, invocation: { errors: [error] } }; if (invoke) await definition.action({}); return { supported: true, invocation: { errors: [] } }; }) }; return { adapter, chat }; }
it('probes without chat mutation and always unregisters', async () => { const h = host(); await expect(runDynamicToolProbe(h.adapter)).resolves.toMatchObject({ supported: true }); expect(h.chat).toEqual([{ is_user: true, mes: 'existing' }]); expect(h.adapter.unregisterTool).toHaveBeenCalledWith('DualModelCapabilityProbe'); });
it('requires successful local action and best-effort unregister', async () => { await expect(runDynamicToolProbe(host({ invoke: false }).adapter)).resolves.toMatchObject({ supported: false }); await expect(runDynamicToolProbe(host({ error: new Error('bad') }).adapter)).resolves.toMatchObject({ supported: false }); const h = host(); h.adapter.unregisterTool.mockImplementation(() => { throw new Error('cleanup'); }); await expect(runDynamicToolProbe(h.adapter)).resolves.toMatchObject({ supported: true }); });
it('unregisters after a registration attempt throws and preserves the registration reason', async () => {
    const h = host(); h.adapter.registerTool.mockImplementation(() => { throw new Error('register failed'); });
    await expect(runDynamicToolProbe(h.adapter)).resolves.toMatchObject({ supported: false, reason: 'register failed' });
    expect(h.adapter.probeMainTool).not.toHaveBeenCalled(); expect(h.adapter.unregisterTool).toHaveBeenCalledOnce();
});
it('rolls back an optimistic supported probe when settings persistence fails', async () => {
    const h = host(); const settings = {}; h.adapter.getSettings = () => settings; h.adapter.getMainApiModelLabel = () => 'api:model'; h.adapter.saveSettings = vi.fn(async () => { throw new Error('disk'); });
    await expect(runDynamicToolProbe(h.adapter)).resolves.toMatchObject({ supported: false, reason: 'Probe persistence failed: disk' });
    expect(settings.toolProbe).toMatchObject({ supported: false, reason: 'Probe persistence failed: disk', apiModelLabel: 'api:model' });
});
