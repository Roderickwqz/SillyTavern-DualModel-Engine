import { describe, expect, it, vi } from 'vitest';
import { createSTAdapter } from '../../src/st-adapter.js';

describe('createSTAdapter', () => {
    it('sends Recorder requests without changing the selected main profile', async () => {
        const sendRequest = vi.fn().mockResolvedValue({ content: '{"base_version":0,"operations":[]}' });
        const host = {
            getContext: () => ({ chatId: 'chat-a', groupId: null, chatMetadata: {}, chat: [] }),
            sendRequest,
            getProfiles: () => [{ id: 'recorder', name: 'Recorder' }],
        };
        const adapter = createSTAdapter(host);

        await adapter.requestProfile('recorder', [{ role: 'user', content: 'state' }], 800, { stream: false });

        expect(sendRequest).toHaveBeenCalledWith(
            'recorder',
            expect.any(Array),
            800,
            expect.objectContaining({ stream: false }),
            {},
        );
    });
});
