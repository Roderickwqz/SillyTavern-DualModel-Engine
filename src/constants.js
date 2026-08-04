export const NAMESPACE = 'dualModelEngine';

export const DATA_SCHEMA_VERSION = 1;

export const DEFAULT_CONFIG = Object.freeze({
    enabled: false,
    recorderProfileId: '',
    rulePresetId: 'narrative',
    updatePolicy: 'after-each-reply',
    adjudication: 'automatic-tool',
    injectionBudget: 1200,
    showStatusBar: true,
});

export const PRESET_LIMITS = Object.freeze({
    maxBytes: 262144,
    maxDepth: 20,
    maxProperties: 500,
    maxItems: 1000,
});
