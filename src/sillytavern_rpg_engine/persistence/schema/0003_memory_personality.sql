CREATE TABLE facts (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    fact_type TEXT NOT NULL
        CHECK (fact_type IN ('identity', 'commitment', 'quest', 'conflict',
                             'rule_consequence', 'general')),
    fact_key TEXT NOT NULL,
    content TEXT NOT NULL,
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    audiences_json TEXT NOT NULL,
    valid_from INTEGER NOT NULL,
    valid_until INTEGER,
    superseded_by TEXT REFERENCES facts(id),
    turn_id TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX facts_current_key
    ON facts(entity_id, fact_key) WHERE valid_until IS NULL;

CREATE INDEX facts_campaign_current
    ON facts(campaign_id, entity_id) WHERE valid_until IS NULL;

CREATE TABLE memory_events (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    turn_id TEXT,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('general', 'scene', 'identity', 'commitment',
                              'conflict', 'quest', 'rule_consequence',
                              'personality_shift')),
    content TEXT NOT NULL,
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    audiences_json TEXT NOT NULL,
    location_entity_id TEXT REFERENCES entities(id),
    source TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

CREATE INDEX memory_events_branch
    ON memory_events(campaign_id, branch_id);

CREATE TABLE memory_event_participants (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES memory_events(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, entity_id)
);

-- Plain FTS5 table (no external content): FTS5 validates content_rowid
-- against PRAGMA table_info of the content table, and memory_events.id is a
-- TEXT primary key, so its implicit rowid is not visible there. The index is
-- maintained manually in the same transaction as each event insert instead.
CREATE VIRTUAL TABLE memory_events_fts USING fts5(
    content,
    tokenize='trigram'
);

CREATE TABLE relationships (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    from_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    to_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    value_json TEXT NOT NULL,
    audiences_json TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    updated_turn_id TEXT,
    PRIMARY KEY (from_entity_id, to_entity_id, dimension)
);

CREATE TABLE trait_events (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    trait_key TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('normal', 'important', 'major')),
    before_value REAL NOT NULL,
    delta REAL NOT NULL,
    after_value REAL NOT NULL,
    cause TEXT NOT NULL,
    turn_id TEXT,
    source TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id, trait_key)
        REFERENCES attribute_definitions(campaign_id, key) ON DELETE CASCADE
);

CREATE UNIQUE INDEX trait_events_turn
    ON trait_events(entity_id, trait_key, turn_id) WHERE turn_id IS NOT NULL;

CREATE INDEX trait_events_entity
    ON trait_events(campaign_id, entity_id, trait_key);

CREATE TABLE development_arcs (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    label TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    start_turn_id TEXT,
    end_turn_id TEXT,
    opened_state_version INTEGER NOT NULL,
    closed_state_version INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX development_arcs_open
    ON development_arcs(entity_id, dimension) WHERE closed_state_version IS NULL;

CREATE TABLE memory_summaries (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    scope TEXT NOT NULL
        CHECK (scope IN ('character', 'relationship', 'quest', 'plotline')),
    scope_key TEXT NOT NULL,
    content TEXT NOT NULL,
    audiences_json TEXT NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, branch_id, scope, scope_key),
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);
