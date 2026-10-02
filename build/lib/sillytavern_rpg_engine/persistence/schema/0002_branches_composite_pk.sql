-- Destructive for populated databases: with PRAGMA foreign_keys=ON the DROP
-- TABLE branches step cascade-deletes dependent rows (pending_proposals,
-- audit_events, state_snapshots) before their copies run, so apply only to
-- fresh bootstrap instances. Future data-bearing migrations must reorder or
-- backfill instead.

CREATE TABLE branches_v2 (
    id TEXT NOT NULL,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    parent_branch_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, id),
    FOREIGN KEY (campaign_id, parent_branch_id)
        REFERENCES branches_v2(campaign_id, id)
);

INSERT INTO branches_v2 (id, campaign_id, parent_branch_id, status, created_at)
SELECT id, campaign_id, parent_branch_id, status, created_at FROM branches;

DROP TABLE branches;

ALTER TABLE branches_v2 RENAME TO branches;

CREATE TABLE pending_proposals_v2 (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    base_state_version INTEGER NOT NULL,
    operation_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'stale')),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

INSERT INTO pending_proposals_v2 (
    id, campaign_id, branch_id, base_state_version, operation_json,
    reason, status, created_at, resolved_at
)
SELECT
    id, campaign_id, branch_id, base_state_version, operation_json,
    reason, status, created_at, resolved_at
FROM pending_proposals;

DROP TABLE pending_proposals;

ALTER TABLE pending_proposals_v2 RENAME TO pending_proposals;

CREATE TABLE audit_events_v2 (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

INSERT INTO audit_events_v2 (
    id, campaign_id, branch_id, state_version, event_type,
    source, payload_json, created_at
)
SELECT
    id, campaign_id, branch_id, state_version, event_type,
    source, payload_json, created_at
FROM audit_events;

DROP TABLE audit_events;

ALTER TABLE audit_events_v2 RENAME TO audit_events;

CREATE TABLE state_snapshots_v2 (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, branch_id, state_version),
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

INSERT INTO state_snapshots_v2 (
    campaign_id, branch_id, state_version, snapshot_json, created_at
)
SELECT
    campaign_id, branch_id, state_version, snapshot_json, created_at
FROM state_snapshots;

DROP TABLE state_snapshots;

ALTER TABLE state_snapshots_v2 RENAME TO state_snapshots;
