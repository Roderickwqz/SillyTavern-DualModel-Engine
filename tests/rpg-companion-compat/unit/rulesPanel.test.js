import { describe, expect, it } from 'vitest';
import { renderDndCombatPanel, selectRulesPanel } from '../../../extensions/rpg-companion-compat/src/compat/rulesPanel.js';

describe('rulesPanel', () => {
    it('selects panel by backend rules metadata', () => {
        expect(selectRulesPanel({ mode: 'narrative', enabled: false })).toBe('narrative');
        expect(selectRulesPanel({ mode: 'dnd-2024', enabled: true })).toBe('dnd');
        expect(selectRulesPanel({ mode: 'custom', enabled: true })).toBe('custom');
    });

    it('renders combat summary read-only', () => {
        const html = renderDndCombatPanel({
            round: 2,
            active_entity_id: 'goblin-1',
            order: ['player', 'goblin-1'],
        });
        expect(html).toContain('Round 2');
        expect(html).toContain('goblin-1');
        expect(html).not.toContain('contenteditable');
    });
});
