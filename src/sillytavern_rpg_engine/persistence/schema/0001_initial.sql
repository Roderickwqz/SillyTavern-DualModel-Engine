CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    rules_mode TEXT NOT NULL DEFAULT 'narrative'
        CHECK (rules_mode IN ('narrative', 'dnd-2024', 'custom')),
    rules_enabled INTEGER NOT NULL DEFAULT 0 CHECK (rules_enabled IN (0, 1)),
    rules_version TEXT,
    custom_preset_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE branches (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    parent_branch_id TEXT REFERENCES branches(id),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id, id)
);

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    age_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (age_status IN ('adult', 'minor', 'unknown')),
    created_state_version INTEGER NOT NULL,
    UNIQUE(campaign_id, normalized_name)
);

CREATE TABLE entity_aliases (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    PRIMARY KEY(campaign_id, normalized_alias)
);

CREATE TABLE attribute_definitions (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    label TEXT NOT NULL,
    category TEXT NOT NULL,
    value_type TEXT NOT NULL,
    display TEXT NOT NULL,
    audiences_json TEXT NOT NULL,
    minimum REAL,
    maximum REAL,
    enum_values_json TEXT NOT NULL DEFAULT '[]',
    unit TEXT,
    created_state_version INTEGER NOT NULL,
    PRIMARY KEY(campaign_id, key)
);

CREATE TABLE attribute_aliases (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    attribute_key TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    PRIMARY KEY(campaign_id, normalized_alias),
    FOREIGN KEY(campaign_id, attribute_key)
        REFERENCES attribute_definitions(campaign_id, key) ON DELETE CASCADE
);

CREATE TABLE attribute_values (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    attribute_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    updated_turn_id TEXT,
    PRIMARY KEY(entity_id, attribute_key),
    FOREIGN KEY(campaign_id, attribute_key)
        REFERENCES attribute_definitions(campaign_id, key) ON DELETE CASCADE
);

CREATE TABLE pending_proposals (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    base_state_version INTEGER NOT NULL,
    operation_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'stale')),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    state_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE jsonl_outbox (
    event_id TEXT PRIMARY KEY REFERENCES audit_events(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    exported_at TEXT
);

CREATE TABLE state_snapshots (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    state_version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, branch_id, state_version)
);
