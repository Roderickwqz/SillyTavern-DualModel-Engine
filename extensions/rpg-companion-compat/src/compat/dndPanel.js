/** D&D combat summary rendering for the compat read-only panel (spec §12.3). */
export function renderDndCombatPanel(combat) {
    if (!combat) return '';
    const round = combat.round ?? '?';
    const active = combat.active_entity_id ?? '—';
    const order = (combat.order ?? []).join(' → ');
    return `<section class="rpg-dnd-combat" data-readonly="true">` +
        `<div class="rpg-dnd-combat-round">Round ${round}</div>` +
        `<div class="rpg-dnd-combat-active">Active: ${active}</div>` +
        `<div class="rpg-dnd-combat-order">${order}</div>` +
        `</section>`;
}
