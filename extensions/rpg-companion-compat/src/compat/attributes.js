function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
}

export function renderAttribute(attribute, { readOnly = true } = {}) {
    const label = escapeHtml(attribute.label ?? attribute.key);
    const display = attribute.display ?? 'text';
    const value = attribute.value;
    const readOnlyAttr = readOnly ? ' data-readonly="true"' : '';
    switch (display) {
    case 'bar':
    case 'progress': {
        const max = Number(attribute.max ?? 100) || 100;
        const num = Number(value ?? 0);
        const pct = Math.max(0, Math.min(100, Math.round((num / max) * 100)));
        return `<div class="rpg-attr rpg-attr-${display}"${readOnlyAttr}>` +
            `<div class="rpg-attr-label">${label}</div>` +
            `<div class="rpg-attr-bar"><span style="width:${pct}%"></span></div>` +
            `<div class="rpg-attr-value">${escapeHtml(num)} / ${escapeHtml(max)}</div>` +
            `</div>`;
    }
    case 'badge':
        return `<span class="rpg-attr rpg-attr-badge"${readOnlyAttr}>${label}: ${escapeHtml(value)}</span>`;
    case 'number':
        return `<div class="rpg-attr rpg-attr-number"${readOnlyAttr}>${label}: ${escapeHtml(value)}</div>`;
    case 'list': {
        const items = Array.isArray(value) ? value : [];
        const lis = items.map((item) => `<li>${escapeHtml(item)}</li>`).join('');
        return `<div class="rpg-attr rpg-attr-list"${readOnlyAttr}>` +
            `<div class="rpg-attr-label">${label}</div><ul>${lis}</ul></div>`;
    }
    case 'text':
    default:
        return `<div class="rpg-attr rpg-attr-text rpg-attr-unknown-display"` +
            ` data-display="${escapeHtml(display)}"${readOnlyAttr}>` +
            `<div class="rpg-attr-label">${label}</div>` +
            `<div class="rpg-attr-value">${escapeHtml(value)}</div></div>`;
    }
}

export function renderAttributeList(attributes, options = {}) {
    const grouped = new Map();
    for (const attribute of attributes ?? []) {
        const category = attribute.category ?? 'other';
        if (!grouped.has(category)) grouped.set(category, []);
        grouped.get(category).push(attribute);
    }
    return [...grouped.entries()].map(([category, items]) => {
        const body = items.map((item) => renderAttribute(item, options)).join('');
        return `<section class="rpg-attr-category" data-category="${escapeHtml(category)}">${body}</section>`;
    }).join('');
}
