ALTER TABLE campaigns ADD COLUMN last_active_branch_id TEXT;

UPDATE campaigns SET last_active_branch_id = 'main' WHERE last_active_branch_id IS NULL;
