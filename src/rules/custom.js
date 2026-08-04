import { decodePointer } from '../json-patch.js';

function deepFreeze(value, seen = new WeakSet()) {
    if (!value || typeof value !== 'object' || seen.has(value)) return value;
    seen.add(value); for (const child of Object.values(value)) deepFreeze(child, seen); return Object.freeze(value);
}
function readAt(root, path) { return decodePointer(path).reduce((value, key) => value?.[key], root); }
function writeAt(root, path, value) { const parts = decodePointer(path); const key = parts.pop(); const parent = parts.reduce((item, part) => item?.[part], root); if (!parent || key === undefined) throw new Error(`Invalid adapter path: ${path}`); parent[key] = value; }
function encode(value) { return String(value).replace(/~/g, '~0').replace(/\//g, '~1'); }

export function createCustomRuleAdapter(preset) {
    const runtime = structuredClone(preset);
    if (!runtime.d20) return deepFreeze(runtime);
    const d20 = runtime.d20;
    return deepFreeze({ ...runtime, ruleLockedPaths: [d20.actorsPath], skillAbilities: d20.skillAbilities ?? {}, naturalRollPolicy: d20.naturalRollPolicy ?? 'normal',
        validateInvariants(state) { const actors = readAt(state, d20.actorsPath) ?? {}; return Object.entries(actors).flatMap(([id, root]) => { const hp = readAt(root, d20.hpPath); return hp && hp.current <= hp.max ? [] : [{ instancePath: `${d20.actorsPath}/${encode(id)}`, message: 'current HP must not exceed max HP' }]; }); },
        readActor(state, id) { const root = readAt(state, `${d20.actorsPath}/${encode(id)}`); if (!root) return undefined; return { abilities: readAt(root, d20.abilitiesPath), proficiencyBonus: readAt(root, d20.proficiencyBonusPath), proficientSkills: readAt(root, d20.proficientSkillsPath), hp: readAt(root, d20.hpPath), conditions: readAt(root, d20.conditionsPath) }; },
        writeActor(state, id, actor) { const root = readAt(state, `${d20.actorsPath}/${encode(id)}`); writeAt(root, d20.hpPath, structuredClone(actor.hp)); writeAt(root, d20.conditionsPath, structuredClone(actor.conditions)); },
    });
}
