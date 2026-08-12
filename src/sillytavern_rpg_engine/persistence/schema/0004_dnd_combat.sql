CREATE TABLE dice_rolls (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    turn_id TEXT,
    roller_entity_id TEXT,
    purpose TEXT NOT NULL,
    formula TEXT NOT NULL,
    faces_json TEXT NOT NULL,
    modifier INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL,
    dc INTEGER,
    success INTEGER CHECK (success IN (0, 1)),
    critical INTEGER NOT NULL DEFAULT 0 CHECK (critical IN (0, 1)),
    rules_version TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER dice_rolls_no_update BEFORE UPDATE ON dice_rolls
BEGIN
    SELECT RAISE(ABORT, 'dice_rolls is append-only');
END;

CREATE TRIGGER dice_rolls_no_delete BEFORE DELETE ON dice_rolls
BEGIN
    SELECT RAISE(ABORT, 'dice_rolls is append-only');
END;

CREATE TABLE combat_encounters (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    round_number INTEGER NOT NULL DEFAULT 1 CHECK (round_number >= 1),
    active_index INTEGER NOT NULL DEFAULT 0 CHECK (active_index >= 0),
    created_state_version INTEGER NOT NULL,
    ended_state_version INTEGER,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX one_active_encounter
    ON combat_encounters(campaign_id, branch_id) WHERE status = 'active';

CREATE TABLE combatants (
    id TEXT PRIMARY KEY,
    encounter_id TEXT NOT NULL REFERENCES combat_encounters(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    initiative INTEGER NOT NULL,
    action_used INTEGER NOT NULL DEFAULT 0 CHECK (action_used IN (0, 1)),
    bonus_used INTEGER NOT NULL DEFAULT 0 CHECK (bonus_used IN (0, 1)),
    reaction_used INTEGER NOT NULL DEFAULT 0 CHECK (reaction_used IN (0, 1)),
    movement_total INTEGER NOT NULL DEFAULT 0 CHECK (movement_total >= 0),
    movement_used INTEGER NOT NULL DEFAULT 0 CHECK (movement_used >= 0),
    interaction_used INTEGER NOT NULL DEFAULT 0 CHECK (interaction_used IN (0, 1)),
    slot_spent_this_turn INTEGER NOT NULL DEFAULT 0 CHECK (slot_spent_this_turn IN (0, 1)),
    attacks_this_turn INTEGER NOT NULL DEFAULT 0 CHECK (attacks_this_turn >= 0),
    dodging INTEGER NOT NULL DEFAULT 0 CHECK (dodging IN (0, 1)),
    disengaged INTEGER NOT NULL DEFAULT 0 CHECK (disengaged IN (0, 1)),
    hidden INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    help_grants_json TEXT NOT NULL DEFAULT '[]',
    readied_action_json TEXT,
    debuffs_json TEXT NOT NULL DEFAULT '{}',
    mastery_uses_json TEXT NOT NULL DEFAULT '{}',
    concentrating_spell TEXT,
    concentration_rounds INTEGER,
    recharge_json TEXT NOT NULL DEFAULT '[]',
    triggers_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(encounter_id, entity_id)
);

CREATE TABLE entity_conditions (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    condition TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
    source TEXT NOT NULL,
    applied_state_version INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, entity_id, condition)
);
