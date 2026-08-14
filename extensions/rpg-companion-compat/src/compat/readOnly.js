import { extensionSettings } from '../core/state.js';

export function isCompatReadOnly() {
    return extensionSettings.compatMode === true;
}

export function guardReadOnly(root) {
    if (!root || !isCompatReadOnly()) return;
    root.dataset.rpgReadonly = 'true';
    root.querySelectorAll('[contenteditable]').forEach((node) => {
        node.removeAttribute('contenteditable');
    });
    root.querySelectorAll('.editable, [data-editable="true"]').forEach((node) => {
        node.classList.remove('editable');
        node.dataset.editable = 'false';
    });
}
