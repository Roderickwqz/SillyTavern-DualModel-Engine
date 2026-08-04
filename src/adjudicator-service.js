export function createAdjudicatorService(deps) {
    async function preflight(input, requireConfirm) {
        const response = await deps.requestDecision(input);
        const request = response?.decision ?? response;
        if (!request?.required) return { strategy: input.strategy, required: false, injectedText: '' };
        const checkInput = { ...request }; delete checkInput.required;
        const validation = deps.validateInput(checkInput);
        if (!validation?.ok) throw new Error(JSON.stringify(validation?.errors ?? ['Invalid adjudicator decision']));
        if (requireConfirm && !await deps.confirm(checkInput)) return { strategy: 'confirm', required: false, cancelled: true, injectedText: '' };
        const check = await deps.stageCheck(checkInput, input);
        return { strategy: requireConfirm ? 'confirm' : 'enforced-preflight', required: true, check, injectedText: deps.formatCheck(check) };
    }
    return {
        async resolveBeforeGeneration(input) {
            if (input.strategy === 'manual') return { strategy: 'manual', required: false, injectedText: '' };
            const probe = deps.getToolProbe?.() ?? deps.toolProbe;
            if (input.strategy === 'automatic-tool' && probe?.supported && (!deps.getMainApiModelLabel || probe.apiModelLabel === deps.getMainApiModelLabel())) return { strategy: 'automatic-tool', required: false, injectedText: '' };
            const strategy = input.strategy === 'automatic-tool' ? 'enforced-preflight' : input.strategy;
            return preflight({ ...input, strategy }, input.strategy === 'confirm');
        },
        resolveManual: async input => { const validation = deps.validateInput(input); if (!validation?.ok) throw new Error(JSON.stringify(validation?.errors ?? ['Invalid manual check'])); return deps.resolveManualCheck(input); },
    };
}
