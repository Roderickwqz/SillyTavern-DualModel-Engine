ALTER TABLE turns ADD COLUMN lineage_hash_before TEXT;
ALTER TABLE turns ADD COLUMN lineage_hash_after TEXT;
ALTER TABLE turns ADD COLUMN response_hash TEXT;
ALTER TABLE turns ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'detached'));

CREATE INDEX idx_turns_lineage_after
    ON turns(campaign_id, lineage_hash_after)
    WHERE status = 'active';

CREATE TABLE branch_heads (
    campaign_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    state_version INTEGER NOT NULL CHECK (state_version >= 0),
    latest_turn_id TEXT REFERENCES turns(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, branch_id),
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

INSERT INTO branch_heads(campaign_id, branch_id, state_version, latest_turn_id, updated_at)
SELECT c.id, 'main', c.state_version, (
    SELECT t.id FROM turns t
    WHERE t.campaign_id = c.id AND t.branch_id = 'main'
    ORDER BY t.created_at DESC LIMIT 1
), c.updated_at
FROM campaigns c
WHERE true
ON CONFLICT(campaign_id, branch_id) DO NOTHING;
