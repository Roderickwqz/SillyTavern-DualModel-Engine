import stateSchema from '../../schemas/state.schema.json';

export const narrativePreset = Object.freeze({
    id: 'narrative',
    name: 'Narrative',
    presetVersion: 1,
    stateSchema,
    initialState: {
        version: 0,
        scene: { location: '', time: '' },
        characters: {},
        inventory: [], quests: [], world_facts: [], promises: [], secrets: [],
        open_threads: [], director_hints: [],
    },
    allowedPaths: ['/scene', '/characters', '/inventory', '/quests', '/world_facts', '/promises', '/secrets', '/open_threads', '/director_hints'],
    lockedPaths: ['/version'],
    injection: [
        { path: '/scene', label: 'scene', priority: 100, required: true },
        { path: '/characters', label: 'characters', priority: 100, required: true },
        { path: '/quests', label: 'quests', priority: 90, required: true },
        { path: '/promises', label: 'promises', priority: 90, required: true },
        { path: '/open_threads', label: 'open_threads', priority: 50, required: false },
        { path: '/director_hints', label: 'director_hints', priority: 10, required: false },
    ],
});
