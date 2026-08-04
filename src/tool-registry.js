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
    const authorized = generation => generation?.effectiveConfig?.enabled && generation.effectiveConfig.rulePresetId !== 'narrative' && generation.effectiveConfig.adjudication === 'automatic-tool';
    const discard = generation => { generation.ruleToolFailed = true; generation.pendingRuleRecords.length = 0; generation.pendingRuleEffects.length = 0; };
    const enqueue = (generation, work) => {
        generation.ruleToolTail ??= Promise.resolve();
        const run = generation.ruleToolTail.catch(() => undefined).then(async () => {
            if (!activeIdentity(getActiveGeneration, generation)) throw staleGeneration();
            if (!authorized(generation)) throw new Error('Rule tool is not authorized for this generation');
            return work();
        });
        generation.ruleToolTail = run;
        return run.catch(error => { discard(generation); throw error; });
    };
    const sameCheck = (generation, key, work) => {
        generation.ruleToolInflight ??= new Map();
        if (!generation.ruleToolInflight.has(key)) {
            generation.ruleToolInflight.set(key, enqueue(generation, work).finally(() => generation.ruleToolInflight.delete(key)));
        }
        return generation.ruleToolInflight.get(key);
    };
    const checkDefinition = {
        name: names[0], displayName: 'Resolve D20 Check', description: 'Resolve a formal story check using authoritative character state. Never provide dice or modifiers.', parameters: checkSchema, shouldRegister: enabled, stealth: false,
        formatMessage: input => `D20: ${input.actor} — ${input.action}`,
        action: async input => {
            const generation = getActiveGeneration(); if (!generation) throw new Error('No active generation');
            const validation = validateCheck(input); if (!validation.ok) { discard(generation); throw new Error(JSON.stringify(validation.errors)); }
            const key = `check:${signature(input, generation.userMessageId)}`;
            return sameCheck(generation, key, async () => {
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
            const generation = getActiveGeneration(); if (!generation) throw new Error('No active generation');
            const validation = validateDamage(input); if (!validation.ok) { discard(generation); throw new Error(JSON.stringify(validation.errors)); }
            return enqueue(generation, async () => {
                const state = structuredClone(generation.pendingRuleEffects.at(-1)?.nextState ?? generation.baseSnapshot);
                const resolved = await resolveDamage(input, state); if (!activeIdentity(getActiveGeneration, generation)) throw staleGeneration();
                const record = ledger.createRecord({ kind: 'damage', branchId: generation.branchId, request: structuredClone(input), result: structuredClone(resolved.audit) });
                generation.pendingRuleRecords.push(record); generation.pendingRuleEffects.push({ record, nextState: structuredClone(resolved.state) }); return record;
            });
        },
    };
    const registered = new Set();
    return { register() { for (const definition of [checkDefinition, damageDefinition]) try { adapter.registerTool(definition); registered.add(definition.name); } catch (error) { let cleanupError; for (const name of [...registered]) try { adapter.unregisterTool(name); registered.delete(name); } catch (failure) { cleanupError ??= failure; } throw error ?? cleanupError; } }, unregister() { let first; for (const name of [...registered]) try { adapter.unregisterTool(name); registered.delete(name); } catch (error) { first ??= error; } if (first) throw first; } };
}
