export { renderDndCombatPanel } from './dndPanel.js';

export function selectRulesPanel(rules = {}) {
    if (rules.mode === 'dnd-2024' && rules.enabled) return 'dnd';
    if (rules.mode === 'custom' && rules.enabled) return 'custom';
    return 'narrative';
}

export function applyRulesPanelVisibility({ rules, combat }, containers) {
    const panel = selectRulesPanel(rules);
    containers.narrative?.classList.toggle('rpg-hidden', panel !== 'narrative');
    containers.dnd?.classList.toggle('rpg-hidden', panel !== 'dnd');
    containers.custom?.classList.toggle('rpg-hidden', panel !== 'custom');
    if (containers.dnd && panel === 'dnd') {
        containers.dnd.innerHTML = renderDndCombatPanel(combat);
    }
}
