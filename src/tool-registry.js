import d20Schema from '../schemas/d20.schema.json';

const checkSchema = { $ref: '#/$defs/checkInput', ...d20Schema };
const damageSchema = { $ref: '#/$defs/damageInput', ...d20Schema };
export { checkSchema as d20ToolInputSchema, damageSchema as d20DamageInputSchema };

function signature(input, userMessageId) { return JSON.stringify([userMessageId, input.actor, input.action.trim(), input.ability, input.skill, input.dc, input.advantage]); }
function activeIdentity(getActiveGeneration, expected) { return getActiveGeneration() === expected; }
function staleGeneration() { return new Error('active generation changed'); }

export function createToolRegistry({ adapter, getConfig, getActiveGeneration, validateCheck, validateDamage, resolveCheck, resolveDamage, ledger }) {
    const names = ['DualModelResolveD20Check', 'DualModelApplyD20Damage'];
    const enabled = () => { const config = getActiveGeneration()?.effectiveConfig ?? getConfig(); return Boolean(getActiveGeneration()) && config?.enabled && config.rulePresetId !== 'narrative' && config.adjudication === 'automatic-tool'; };
    const sameCall = (generation, key, work) => {
        generation.ruleToolInflight ??= new Map();
        if (!generation.ruleToolInflight.has(key)) {
            generation.ruleToolTail ??= Promise.resolve();
            const run = generation.ruleToolTail.catch(() => undefined).then(work);
            generation.ruleToolTail = run;
            generation.ruleToolInflight.set(key, run.finally(() => generation.ruleToolInflight.delete(key)));
        }
        return generation.ruleToolInflight.get(key);
    };
    const checkDefinition = {
        name: names[0], displayName: 'Resolve D20 Check', description: 'Resolve a formal story check using authoritative character state. Never provide dice or modifiers.', parameters: checkSchema, shouldRegister: enabled, stealth: false,
        formatMessage: input => `D20: ${input.actor} — ${input.action}`,
        action: async input => {
            const validation = validateCheck(input); if (!validation.ok) throw new Error(JSON.stringify(validation.errors));
            const generation = getActiveGeneration(); if (!generation) throw new Error('No active generation');
            const key = `check:${signature(input, generation.userMessageId)}`;
            return sameCall(generation, key, async () => {
                const existing = generation.pendingRuleRecords.find(record => record.kind === 'check' && record.signature === key.slice(6)); if (existing) return existing;
                const reusable = generation.baseBranchId && ledger.findReusable({ baseBranchId: generation.baseBranchId, signature: key.slice(6) });
                if (reusable) { if (!activeIdentity(getActiveGeneration, generation)) throw staleGeneration(); generation.pendingRuleRecords.push(reusable); return reusable; }
                if (generation.ruleReplayMode === 'reuse-only') throw new Error('Ordinary regeneration cannot create or reroll a formal check; use explicit reroll');
                const result = await resolveCheck(input, structuredClone(generation.pendingRuleEffects.at(-1)?.nextState ?? generation.baseSnapshot));
                if (!activeIdentity(getActiveGeneration, generation)) throw staleGeneration();
                const record = ledger.createRecord({ kind: 'check', branchId: generation.branchId, signature: key.slice(6), request: structuredClone(input), result: structuredClone(result) }); generation.pendingRuleRecords.push(record); return record;
            });
        },
    };
    const damageDefinition = {
        name: names[1], displayName: 'Apply D20 Damage', description: 'Roll and apply authoritative damage to a target. Never provide rolled values or HP totals.', parameters: damageSchema, shouldRegister: enabled, stealth: false,
        formatMessage: input => `Damage: ${input.target} — ${input.expression}`,
        action: async input => {
            const validation = validateDamage(input); if (!validation.ok) throw new Error(JSON.stringify(validation.errors));
            const generation = getActiveGeneration(); if (!generation) throw new Error('No active generation');
            const key = `damage:${JSON.stringify(input)}`;
            return sameCall(generation, key, async () => {
                const state = structuredClone(generation.pendingRuleEffects.at(-1)?.nextState ?? generation.baseSnapshot);
                const resolved = await resolveDamage(input, state); if (!activeIdentity(getActiveGeneration, generation)) throw staleGeneration();
                const record = ledger.createRecord({ kind: 'damage', branchId: generation.branchId, request: structuredClone(input), result: structuredClone(resolved.audit) });
                generation.pendingRuleRecords.push(record); generation.pendingRuleEffects.push({ record, nextState: structuredClone(resolved.state) }); return record;
            });
        },
    };
    return { register() { try { adapter.registerTool(checkDefinition); adapter.registerTool(damageDefinition); } catch (error) { try { adapter.unregisterTool(names[0]); } catch { /* preserve registration error */ } throw error; } }, unregister() { let first; for (const name of names) try { adapter.unregisterTool(name); } catch (error) { first ??= error; } if (first) throw first; } };
}
