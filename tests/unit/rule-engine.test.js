import { describe, expect, it } from 'vitest';
import Ajv from 'ajv';
import d20Schema from '../../schemas/d20.schema.json';
import { createRuleEngine } from '../../src/rule-engine.js';
import { d20LitePreset } from '../../src/rules/d20-lite.js';
import { createStateValidator } from '../../src/state-validator.js';
import { d20TestPreset, d20TestState } from '../fixtures/d20.js';

describe('D20 rule engine', () => {
    it('uses state modifiers rather than model-provided numbers', () => {
        const engine = createRuleEngine({ nextUint32: () => 14, preset: d20TestPreset() });
        const result = engine.resolveCheck({ actor: 'player', action: 'pick lock', ability: 'dexterity', skill: 'sleight_of_hand', dc: 18, advantage: 'normal' }, d20TestState());
        expect(result).toMatchObject({ rolls: [15], selectedRoll: 15, abilityModifier: 2, proficiencyBonus: 2, total: 19, outcome: 'success' });
    });

    it.each([
        ['advantage', [2, 18], 19],
        ['disadvantage', [2, 18], 3],
    ])('selects %s correctly', (advantage, values, selectedRoll) => {
        const engine = createRuleEngine({ nextUint32: () => values.shift(), preset: d20TestPreset() });
        expect(engine.resolveCheck({ actor: 'player', action: 'test', ability: 'dexterity', skill: 'sleight_of_hand', dc: 10, advantage }, d20TestState()).selectedRoll).toBe(selectedRoll);
    });

    it('applies proficiency only when the actor has the requested skill', () => {
        const engine = createRuleEngine({ nextUint32: () => 9, preset: d20TestPreset() });
        expect(engine.resolveCheck({ actor: 'player', action: 'notice', ability: 'wisdom', skill: 'perception', dc: 10, advantage: 'normal' }, d20TestState())).toMatchObject({ abilityModifier: 0, proficiencyBonus: 0, total: 10, outcome: 'success' });
    });

    it('rejects unknown skills and ability-skill mismatches before rolling', () => {
        const nextUint32 = () => { throw new Error('should not roll'); };
        const engine = createRuleEngine({ nextUint32, preset: d20TestPreset() });
        const input = { actor: 'player', action: 'lift', ability: 'dexterity', skill: 'athletics', dc: 10, advantage: 'normal' };
        expect(() => engine.resolveCheck(input, d20TestState())).toThrow('Ability does not match skill: athletics');
        expect(() => engine.resolveCheck({ ...input, ability: 'strength', skill: 'unknown' }, d20TestState())).toThrow('Unknown skill: unknown');
    });

    it('makes natural 1 and 20 authoritative critical outcomes', () => {
        const engine = createRuleEngine({ nextUint32: (() => { const values = [0, 19]; return () => values.shift(); })(), preset: d20TestPreset() });
        const input = { actor: 'player', action: 'test', ability: 'dexterity', skill: 'sleight_of_hand', dc: 40, advantage: 'normal' };
        expect(engine.resolveCheck(input, d20TestState()).outcome).toBe('critical-failure');
        expect(engine.resolveCheck({ ...input, dc: 1 }, d20TestState()).outcome).toBe('critical-success');
    });

    it('refuses a missing actor without mutating state', () => {
        const state = d20TestState();
        const engine = createRuleEngine({ nextUint32: () => 0, preset: d20TestPreset() });
        expect(() => engine.resolveCheck({ actor: 'missing', ability: 'dexterity', skill: 'sleight_of_hand', dc: 10, advantage: 'normal' }, state)).toThrow('Unknown actor: missing');
        expect(state).toEqual(d20TestState());
    });

    it('absorbs temporary HP and clamps current HP to zero without mutating input', () => {
        const before = d20TestState();
        const engine = createRuleEngine({ nextUint32: () => 5, preset: d20TestPreset() });
        const result = engine.applyDamage({ target: 'player', expression: '2d6+3' }, before);
        expect(result.damage).toMatchObject({ total: 15, absorbed: 3 });
        expect(result.state.actors.player.hp).toMatchObject({ current: 0, temporary: 0 });
        expect(before).toEqual(d20TestState());
    });

    it('clamps negative damage to zero without increasing HP or temporary HP', () => {
        const before = d20TestState();
        const engine = createRuleEngine({ nextUint32: () => 0, preset: d20TestPreset() });
        const result = engine.applyDamage({ target: 'player', expression: '1d2-999' }, before);
        expect(result.damage).toMatchObject({ rawTotal: -998, total: 0, absorbed: 0 });
        expect(result.state.actors.player.hp).toEqual(before.actors.player.hp);
    });

    it('rejects damage for a missing actor before cloning or consuming randomness', () => {
        const nextUint32 = () => { throw new Error('should not roll'); };
        const engine = createRuleEngine({ nextUint32, preset: d20TestPreset() });
        expect(() => engine.applyDamage({ target: 'missing', expression: '1d6' }, d20TestState())).toThrow('Unknown actor: missing');
    });

    it('does not consume randomness when cloning an invalid mutable state fails', () => {
        const state = d20TestState();
        state.uncloneable = () => {};
        const engine = createRuleEngine({ nextUint32: () => { throw new Error('should not roll'); }, preset: d20TestPreset() });
        expect(() => engine.applyDamage({ target: 'player', expression: '1d6' }, state)).toThrow();
        expect(state.actors.player.hp).toEqual(d20TestState().actors.player.hp);
    });

    it('validates D20 state bounds and the current HP invariant', () => {
        const validator = createStateValidator({ presets: [d20LitePreset] });
        expect(validator.validateState('d20-lite', d20TestState())).toEqual({ ok: true, errors: [] });
        const invalid = d20TestState();
        invalid.actors.player.hp.current = 11;
        expect(validator.validateState('d20-lite', invalid)).toMatchObject({ ok: false, errors: expect.arrayContaining([expect.objectContaining({ instancePath: '/actors/player/hp/current' })]) });
    });

    it('rejects model-supplied rolls, modifiers, outcomes, and HP through D20 input schemas', () => {
        const ajv = new Ajv({ strict: false });
        const validateCheck = ajv.compile(d20Schema.$defs.checkInput);
        const validateDamage = ajv.compile(d20Schema.$defs.damageInput);
        const check = { actor: 'player', action: 'pick lock', ability: 'dexterity', skill: 'sleight_of_hand', dc: 12, advantage: 'normal', reason: 'locked' };
        const damage = { target: 'player', expression: '1d6', damageType: 'piercing', reason: 'trap' };
        expect(validateCheck({ ...check, modifier: 99, roll: 20, outcome: 'success' })).toBe(false);
        expect(validateDamage({ ...damage, hp: 0, total: 999 })).toBe(false);
    });
});
