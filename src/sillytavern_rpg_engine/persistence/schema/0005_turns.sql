CREATE TABLE turns (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    parent_turn_id TEXT REFERENCES turns(id),
    intent TEXT NOT NULL CHECK (intent IN ('action', 'query', 'explicit_change')),
    player_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    history_hash TEXT NOT NULL,
    removed_instructions_json TEXT NOT NULL DEFAULT '[]',
    state_before_version INTEGER NOT NULL CHECK (state_before_version >= 0),
    state_after_version INTEGER NOT NULL
        CHECK (state_after_version >= state_before_version),
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

CREATE INDEX idx_turns_campaign_branch ON turns(campaign_id, branch_id, created_at);
CREATE INDEX idx_turns_history_hash ON turns(campaign_id, history_hash);
