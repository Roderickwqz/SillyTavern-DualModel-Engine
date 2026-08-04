export function createAdjudicatorService(deps) {
    async function preflight(input, requireConfirm) {
        const request = await deps.requestDecision(input);
        if (!request?.required) return { strategy: input.strategy, required: false, injectedText: '' };
        const validation = deps.validateInput(request);
        if (!validation?.ok) throw new Error(JSON.stringify(validation?.errors ?? ['Invalid adjudicator decision']));
        if (requireConfirm && !await deps.confirm(request)) return { strategy: 'confirm', required: false, cancelled: true, injectedText: '' };
        const check = await deps.stageCheck(request, input);
        return { strategy: requireConfirm ? 'confirm' : 'enforced-preflight', required: true, check, injectedText: deps.formatCheck(check) };
    }
    return {
        async resolveBeforeGeneration(input) {
            if (input.strategy === 'manual') return { strategy: 'manual', required: false, injectedText: '' };
            if (input.strategy === 'automatic-tool' && deps.toolProbe?.supported) return { strategy: 'automatic-tool', required: false, injectedText: '' };
            const strategy = input.strategy === 'automatic-tool' ? 'enforced-preflight' : input.strategy;
            return preflight({ ...input, strategy }, input.strategy === 'confirm');
        },
        resolveManual: input => deps.resolveManualCheck(input),
    };
}
