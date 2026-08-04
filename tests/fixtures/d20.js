export function d20TestState() {
    return {
        version: 0,
        scene: { location: '', time: '' },
        characters: {}, inventory: [], quests: [], world_facts: [], promises: [], secrets: [],
        open_threads: [], director_hints: [],
        actors: {
            player: {
                abilities: { strength: 10, dexterity: 14, constitution: 10, intelligence: 10, wisdom: 10, charisma: 10 },
                proficiencyBonus: 2,
                proficientSkills: ['sleight_of_hand'],
                hp: { current: 10, max: 10, temporary: 3 },
                conditions: [],
            },
        },
    };
}

export function d20TestPreset() {
    return {
        naturalRollPolicy: 'critical',
        skillAbilities: { athletics: 'strength', perception: 'wisdom', sleight_of_hand: 'dexterity' },
        readActor: (state, actorId) => state.actors[actorId],
        writeActor: (state, actorId, actor) => { state.actors[actorId] = actor; },
    };
}
