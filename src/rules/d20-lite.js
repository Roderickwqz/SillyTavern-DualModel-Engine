import d20StateSchema from '../../schemas/d20-state.schema.json';
import { narrativePreset } from './narrative.js';

export const d20LitePreset = Object.freeze({
    ...narrativePreset,
    id: 'd20-lite',
    name: 'D20 Lite',
    presetVersion: 1,
    stateSchema: d20StateSchema,
    initialState: { ...structuredClone(narrativePreset.initialState), actors: {} },
    naturalRollPolicy: 'critical',
    skillAbilities: Object.freeze({
        acrobatics: 'dexterity', athletics: 'strength', investigation: 'intelligence',
        perception: 'wisdom', persuasion: 'charisma', sleight_of_hand: 'dexterity', stealth: 'dexterity',
    }),
    ruleLockedPaths: ['/actors'],
    injection: [...narrativePreset.injection, { path: '/actors', label: 'actors', priority: 100, required: true }],
    validateInvariants(state) {
        return Object.entries(state.actors).flatMap(([actorId, actor]) => actor.hp.current <= actor.hp.max
            ? [] : [{ instancePath: `/actors/${actorId}/hp/current`, message: 'must not exceed max HP' }]);
    },
    readActor: (state, actorId) => state.actors[actorId],
    writeActor: (state, actorId, actor) => { state.actors[actorId] = actor; },
});
