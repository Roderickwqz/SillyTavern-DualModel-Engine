import { describe, expect, it } from 'vitest';
import { renderAttributeList } from '../../../extensions/rpg-companion-compat/src/compat/attributes.js';

describe('renderAttributeList', () => {
    it('groups by category and renders known display types read-only', () => {
        const html = renderAttributeList([
            { key: 'alchemy', label: '炼金术', category: 'skill', type: 'number', value: 35, max: 100, display: 'bar' },
            { key: 'notes', label: '备注', category: 'lore', type: 'text', value: '谨慎', display: 'text' },
            { key: 'weird', label: '未知', category: 'misc', type: 'text', value: 'x', display: 'future-widget' },
        ], { readOnly: true });
        expect(html).toContain('炼金术');
        expect(html).toContain('rpg-attr-bar');
        expect(html).toContain('谨慎');
        expect(html).toContain('future-widget');
        expect(html).not.toContain('contenteditable="true"');
    });
});
