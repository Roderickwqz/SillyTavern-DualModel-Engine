import { describe, expect, it } from 'vitest';
import { renderPendingProposals } from '../../../extensions/rpg-companion-compat/src/compat/pendingProposals.js';
import { shouldShowStaleWarning } from '../../../extensions/rpg-companion-compat/src/compat/staleWarning.js';

describe('pending + stale UI', () => {
    it('lists pending proposals with chat command hints', () => {
        const html = renderPendingProposals([
            { id: 'prop-1', reason: '剧情推断', base_state_version: 3 },
        ]);
        expect(html).toContain('prop-1');
        expect(html).toContain('确认提案 prop-1');
        expect(html).toContain('拒绝提案 prop-1');
    });

    it('flags stale display on parse failure or version regression', () => {
        expect(shouldShowStaleWarning({ parseFailed: true })).toBe(true);
        expect(shouldShowStaleWarning({
            parsedCompat: { state_version: 2 },
            committedVersion: 5,
            parseFailed: false,
        })).toBe(true);
        expect(shouldShowStaleWarning({
            parsedCompat: { state_version: 6 },
            committedVersion: 5,
            parseFailed: false,
        })).toBe(false);
    });
});
