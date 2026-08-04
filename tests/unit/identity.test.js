import { expect, it } from 'vitest';
import { ensureMessageId, hashText } from '../../src/identity.js';

it('reuses persisted identities and synchronizes every swipe without replacing branches', () => {
    const firstBranch = { branchId: 'first', segments: [] };
    const secondBranch = { branchId: 'second', segments: [] };
    const message = {
        extra: {},
        swipe_info: [
            { extra: { dualModelEngine: { branch: firstBranch } } },
            { extra: { dualModelEngine: { branch: secondBranch } } },
        ],
    };

    expect(ensureMessageId(message, () => 'msg-1')).toBe('msg-1');
    expect(ensureMessageId(message, () => 'msg-2')).toBe('msg-1');
    expect(message.swipe_info[0].extra.dualModelEngine).toEqual({ messageId: 'msg-1', branch: firstBranch });
    expect(message.swipe_info[1].extra.dualModelEngine).toEqual({ messageId: 'msg-1', branch: secondBranch });
});

it('hashes UTF-8 text as a sha256 fingerprint', async () => {
    expect(await hashText('hello 😀')).toBe('sha256:701734ab010f0b6dbb1cd1ce3c474b226d1abff267affc16c2bfd6ba1bb52396');
});
