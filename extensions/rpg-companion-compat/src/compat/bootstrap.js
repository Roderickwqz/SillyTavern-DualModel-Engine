import { renderAttributeList } from './attributes.js';
import { applyRulesPanelVisibility } from './rulesPanel.js';
import { renderPendingProposals } from './pendingProposals.js';
import {
    renderStaleWarning, shouldShowStaleWarning, updateCompatStateVersion,
} from './staleWarning.js';
import { guardReadOnly } from './readOnly.js';
import { cacheCompatTracker } from './lineageCache.js';
import {
    extensionSettings, lastGeneratedData, setLastGeneratedData,
} from '../core/state.js';

export function applyCompatTracker(parseResult, containers, message = { swipe_id: 0 }) {
    const compat = parseResult.compat ?? null;
    setLastGeneratedData({ ...lastGeneratedData, compat });
    if (compat != null) {
        cacheCompatTracker(message, message.swipe_id ?? 0, compat);
    }

    if (containers.staleContainer) {
        const stale = shouldShowStaleWarning({
            parsedCompat: compat,
            committedVersion: extensionSettings.compatStateVersion,
            parseFailed: compat == null && /```json/.test(parseResult.rawText ?? ''),
        });
        containers.staleContainer.innerHTML = stale
            ? renderStaleWarning('Tracker 解析失败或已过期；显示可能落后。后端权威状态未改变。')
            : '';
    }

    if (!compat) return;

    if (containers.userStatsContainer) {
        containers.userStatsContainer.innerHTML = renderAttributeList(
            compat.userStats?.attributes ?? [], { readOnly: true },
        );
        guardReadOnly(containers.userStatsContainer);
    }
    if (containers.infoBoxContainer) {
        const box = compat.infoBox ?? {};
        containers.infoBoxContainer.innerHTML = Object.entries(box)
            .map(([k, v]) => `<div class="rpg-info-field" data-key="${k}">${k}: ${v}</div>`)
            .join('');
        guardReadOnly(containers.infoBoxContainer);
    }
    if (containers.proposalsContainer) {
        containers.proposalsContainer.innerHTML = renderPendingProposals(
            compat.pending_proposals ?? [],
        );
    }
    applyRulesPanelVisibility(
        { rules: compat.rules, combat: compat.combat },
        containers.rulesContainers ?? {},
    );
    updateCompatStateVersion(extensionSettings, compat.state_version);
}

export function initCompatMode() {
    extensionSettings.compatMode = true;
    extensionSettings.generationMode = 'together';
    extensionSettings.autoUpdate = false;
    if (extensionSettings.historyPersistence) {
        extensionSettings.historyPersistence.enabled = false;
    }
}

/** Resolve real panel containers, creating display-only slots for compat extras. */
export function collectCompatContainers() {
    const host = document.getElementById('rpg-user-stats')?.parentElement ?? document.body;
    const ensureSlot = (id) => {
        let node = document.getElementById(id);
        if (!node) {
            node = document.createElement('div');
            node.id = id;
            host.append(node);
        }
        return node;
    };
    return {
        userStatsContainer: document.getElementById('rpg-user-stats'),
        infoBoxContainer: document.getElementById('rpg-info-box'),
        proposalsContainer: ensureSlot('rpg-compat-proposals'),
        staleContainer: ensureSlot('rpg-compat-stale'),
        rulesContainers: {},
    };
}
