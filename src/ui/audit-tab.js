export function renderAudit(container, records = []) {
    container.replaceChildren();
    for (const record of records) {
        const row = document.createElement('article');
        row.className = `dualmodel-audit dualmodel-audit--${record.status ?? 'committed'}`;
        const title = document.createElement('strong'); title.textContent = `${record.kind ?? 'record'}: ${record.action ?? record.path ?? record.reason ?? ''}`;
        const detail = document.createElement('pre'); detail.textContent = JSON.stringify(record, null, 2);
        row.append(title, detail); container.append(row);
    }
}

export function renderStatusBar(container, state, uiFields = [], readPath = () => undefined) {
    container.replaceChildren();
    for (const field of uiFields) { const item = document.createElement('span'); item.textContent = `${field.label}: ${JSON.stringify(readPath(state, field.path))}`; container.append(item); }
}
