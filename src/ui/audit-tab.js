export function renderAudit(container, records = [], { selectedCheckId = null } = {}) {
    container.replaceChildren();
    for (const record of records) {
        const row = document.createElement('article');
        row.className = `dualmodel-audit dualmodel-audit--${record.status ?? 'committed'}`;
        const title = document.createElement('strong'); title.textContent = `${record.kind ?? 'record'}: ${record.action ?? record.path ?? record.reason ?? ''}`;
        const detail = document.createElement('pre'); detail.textContent = JSON.stringify(record, null, 2);
        if (record.kind === 'check' && record.checkId) { const select = document.createElement('input'); select.type = 'radio'; select.name = 'dme-selected-check'; select.dataset.dmeCheckId = record.checkId; select.checked = record.checkId === selectedCheckId; select.setAttribute('aria-label', `Select check ${record.checkId}`); row.append(select); }
        row.append(title, detail); container.append(row);
    }
}

export function renderStatusBar(container, state, uiFields = [], readPath = () => undefined) {
    container.replaceChildren();
    for (const field of uiFields) { const item = document.createElement('span'); item.textContent = `${field.label}: ${JSON.stringify(readPath(state, field.path))}`; container.append(item); }
}
