export function renderDiagnostics(container, value) { container.textContent = JSON.stringify(value ?? [], null, 2); }
