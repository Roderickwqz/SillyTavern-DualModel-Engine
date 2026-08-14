export function shouldShowStaleWarning({ parsedCompat, committedVersion, parseFailed }) {
    if (parseFailed) return true;
    if (!parsedCompat || parsedCompat.state_version == null) return false;
    if (committedVersion == null) return false;
    return parsedCompat.state_version < committedVersion;
}

export function renderStaleWarning(reason) {
    return `<div class="rpg-compat-stale" role="status">${reason}</div>`;
}

export function updateCompatStateVersion(extensionSettings, version) {
    if (Number.isInteger(version)) {
        extensionSettings.compatStateVersion = version;
    }
}
