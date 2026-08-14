function escapeHtml(v) { return String(v).replaceAll('<', '&lt;'); }

export function renderPendingProposals(proposals = []) {
    if (!proposals.length) return '';
    const items = proposals.map((p) =>
        `<li><strong>${escapeHtml(p.id)}</strong> — ${escapeHtml(p.reason ?? '')}` +
        `<div class="rpg-proposal-hint">` +
        `确认提案 ${escapeHtml(p.id)} / 拒绝提案 ${escapeHtml(p.id)}` +
        `</div></li>`,
    ).join('');
    return `<section class="rpg-pending-proposals" data-readonly="true"><ul>${items}</ul></section>`;
}
