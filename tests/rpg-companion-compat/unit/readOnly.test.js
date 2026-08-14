import { beforeEach, describe, expect, it } from 'vitest';
import { guardReadOnly, isCompatReadOnly } from '../../../extensions/rpg-companion-compat/src/compat/readOnly.js';
import { extensionSettings } from '../../../extensions/rpg-companion-compat/src/core/state.js';

describe('readOnly', () => {
    beforeEach(() => {
        document.body.innerHTML = '<div id="panel"><span contenteditable="true" class="editable">HP</span></div>';
        extensionSettings.compatMode = true;
    });

    it('detects compat read-only mode', () => {
        expect(isCompatReadOnly()).toBe(true);
        extensionSettings.compatMode = false;
        expect(isCompatReadOnly()).toBe(false);
    });

    it('strips contenteditable and marks container read-only', () => {
        const panel = document.getElementById('panel');
        guardReadOnly(panel);
        expect(panel.dataset.rpgReadonly).toBe('true');
        expect(panel.querySelector('[contenteditable]')).toBeNull();
    });
});
