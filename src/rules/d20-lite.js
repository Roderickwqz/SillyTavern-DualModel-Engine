import d20StateSchema from '../../schemas/d20-state.schema.json';
import { narrativePreset } from './narrative.js';

function deepFreeze(value, seen = new WeakSet()) {
    if (!value || typeof value !== 'object' || seen.has(value)) return value;
    seen.add(value);
    for (const child of Object.values(value)) deepFreeze(child, seen);
    return Object.freeze(value);
}

export const d20LitePreset = deepFreeze({
    ...narrativePreset,
    id: 'd20-lite',
    name: 'D20 Lite',
    presetVersion: 1,
    stateSchema: structuredClone(d20StateSchema),
    initialState: { ...structuredClone(narrativePreset.initialState), actors: {} },
    naturalRollPolicy: 'critical',
    skillAbilities: Object.freeze({
        acrobatics: 'dexterity', athletics: 'strength', investigation: 'intelligence',
        perception: 'wisdom', persuasion: 'charisma', sleight_of_hand: 'dexterity', stealth: 'dexterity',
    }),
    allowedPaths: structuredClone(narrativePreset.allowedPaths),
    lockedPaths: structuredClone(narrativePreset.lockedPaths),
    ruleLockedPaths: ['/actors'],
    injection: [...structuredClone(narrativePreset.injection), { path: '/actors', label: 'actors', priority: 100, required: true }],
    validateInvariants(state) {
        return Object.entries(state.actors).flatMap(([actorId, actor]) => actor.hp.current <= actor.hp.max
            ? [] : [{ instancePath: `/actors/${actorId}/hp/current`, message: 'must not exceed max HP' }]);
    },
    readActor: (state, actorId) => state.actors[actorId],
    writeActor: (state, actorId, actor) => { state.actors[actorId] = actor; },
});
