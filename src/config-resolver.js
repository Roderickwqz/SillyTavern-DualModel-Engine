import { DEFAULT_CONFIG } from './constants.js';

function definedEntries(value = {}) {
    return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined));
}

export function resolveConfig({ globalConfig = {}, characterConfig = {}, chatConfig = {} }) {
    return Object.freeze({
        ...DEFAULT_CONFIG,
        ...definedEntries(globalConfig),
        ...definedEntries(characterConfig),
        ...definedEntries(chatConfig),
    });
}
