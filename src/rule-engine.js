import { rollDie, rollExpression } from './dice-engine.js';

function abilityModifier(score) {
    return Math.floor((score - 10) / 2);
}

function selectRoll(rolls, advantage) {
    if (advantage === 'advantage') return Math.max(...rolls);
    if (advantage === 'disadvantage') return Math.min(...rolls);
    return rolls[0];
}

function assertCheckAuthority(input, preset) {
    const ability = preset.skillAbilities?.[input.skill];
    if (!ability) throw new Error(`Unknown skill: ${input.skill}`);
    if (input.ability !== ability) throw new Error(`Ability does not match skill: ${input.skill}`);
}

function assertAdvantage(advantage) {
    if (!['normal', 'advantage', 'disadvantage'].includes(advantage)) throw new Error(`Invalid advantage mode: ${advantage}`);
}

function assertActor(actorId, actor) {
    if (!actor) throw new Error(`Unknown actor: ${actorId}`);
}

export function createRuleEngine({ nextUint32, preset }) {
    if (!preset || typeof preset.readActor !== 'function' || typeof preset.writeActor !== 'function') throw new TypeError('A rule preset with actor accessors is required');
    return {
        resolveCheck(input, state) {
            assertCheckAuthority(input, preset);
            assertAdvantage(input.advantage);
            const actor = preset.readActor(state, input.actor);
            assertActor(input.actor, actor);
            const rolls = Array.from({ length: input.advantage === 'normal' ? 1 : 2 }, () => rollDie(20, nextUint32));
            const selectedRoll = selectRoll(rolls, input.advantage);
            const abilityMod = abilityModifier(actor.abilities[input.ability]);
            const proficiencyBonus = actor.proficientSkills.includes(input.skill) ? actor.proficiencyBonus : 0;
            const total = selectedRoll + abilityMod + proficiencyBonus;
            const outcome = preset.naturalRollPolicy === 'critical' && selectedRoll === 20 ? 'critical-success'
                : preset.naturalRollPolicy === 'critical' && selectedRoll === 1 ? 'critical-failure'
                    : total >= input.dc ? 'success' : 'failure';
            return { rolls, selectedRoll, abilityModifier: abilityMod, proficiencyBonus, total, dc: input.dc, outcome };
        },
        applyDamage({ target: actorId, expression }, state) {
            assertActor(actorId, preset.readActor(state, actorId));
            const nextState = structuredClone(state);
            const actor = structuredClone(preset.readActor(nextState, actorId));
            const damage = rollExpression(expression, nextUint32);
            const total = Math.max(0, damage.total);
            const absorbed = Math.min(actor.hp.temporary, total);
            actor.hp.temporary -= absorbed;
            actor.hp.current = Math.max(0, Math.min(actor.hp.max, actor.hp.current - (total - absorbed)));
            preset.writeActor(nextState, actorId, actor);
            return { state: nextState, damage: { ...damage, rawTotal: damage.total, total, absorbed } };
        },
    };
}
