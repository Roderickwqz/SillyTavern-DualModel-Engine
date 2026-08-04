export function renderRules(container, presets = []) {
    container.replaceChildren();
    for (const preset of presets) { const item = document.createElement('div'); item.textContent = `${preset.name ?? preset.id} v${preset.presetVersion ?? ''}`; container.append(item); }
}
