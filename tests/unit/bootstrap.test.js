import { beforeEach, describe, expect, it, vi } from 'vitest';

const runtimeAdapter = {
    getContext: vi.fn(() => ({ groupId: null })),
    listProfiles: vi.fn(() => [{ id: 'recorder', name: 'Recorder' }]),
    canInjectPrompt: true,
    canPersist: true,
    canRegisterTools: true,
    on: vi.fn(),
    off: vi.fn(),
    saveChat: vi.fn(),
    saveSettings: vi.fn(),
};
const createRuntimeAdapter = vi.fn(() => runtimeAdapter);

vi.mock('../../src/st-runtime.js', () => ({ createRuntimeAdapter }));

import { bootstrap } from '../../src/index.js';

describe('bootstrap', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('constructs and starts the injected adapter orchestration without persisting', async () => {
        const adapter = {
            getContext: vi.fn(() => ({ groupId: null })),
            listProfiles: vi.fn(() => [{ id: 'recorder', name: 'Recorder' }]),
            canInjectPrompt: true,
            canPersist: true,
            canRegisterTools: true,
            on: vi.fn(),
            off: vi.fn(),
            saveChat: vi.fn(),
            saveSettings: vi.fn(),
        };

        const result = await bootstrap({ adapter });

        expect(result).toMatchObject({
            name: 'dualModelEngine',
            adapter,
            capabilities: {
                supported: true,
                isGroupChat: false,
                profiles: [{ id: 'recorder', name: 'Recorder' }],
                toolApiAvailable: true,
                promptInjectionAvailable: true,
                persistenceAvailable: true,
                reasons: [],
            },
        });
        expect(createRuntimeAdapter).not.toHaveBeenCalled();
        expect(adapter.on).not.toHaveBeenCalled();
        expect(adapter.off).not.toHaveBeenCalled();
        expect(adapter.saveChat).not.toHaveBeenCalled();
        expect(adapter.saveSettings).not.toHaveBeenCalled();
        expect(result.orchestrator).toBeDefined();
    });

    it('loads the runtime adapter only when one is not injected', async () => {
        const result = await bootstrap();

        expect(createRuntimeAdapter).toHaveBeenCalledOnce();
        expect(result.adapter).toBe(runtimeAdapter);
        expect(result.capabilities.supported).toBe(true);
    });
});
