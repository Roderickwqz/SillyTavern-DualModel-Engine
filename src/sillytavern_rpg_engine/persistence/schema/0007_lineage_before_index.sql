CREATE INDEX idx_turns_lineage_before
    ON turns(campaign_id, lineage_hash_before)
    WHERE status = 'active';
