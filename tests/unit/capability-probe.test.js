import { expect, it } from 'vitest';
import { probeHostCapabilities } from '../../src/capability-probe.js';

it('rejects group chats without declaring the whole host broken', () => {
    const report = probeHostCapabilities({
        getContext: () => ({ groupId: 'group-1' }),
        listProfiles: () => [],
        canInjectPrompt: true,
        canPersist: true,
        canRegisterTools: true,
    });

    expect(report.supported).toBe(false);
    expect(report.isGroupChat).toBe(true);
    expect(report.reasons).toContain('Group chats are not supported');
});
