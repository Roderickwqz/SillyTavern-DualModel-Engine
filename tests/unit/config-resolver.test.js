import { expect, it } from 'vitest';
import { resolveConfig } from '../../src/config-resolver.js';

it('applies chat over character over global without undefined erasing values', () => {
    const result = resolveConfig({
        globalConfig: { enabled: false, injectionBudget: 900, adjudication: 'manual' },
        characterConfig: { enabled: true, injectionBudget: 1100 },
        chatConfig: { adjudication: 'confirm', injectionBudget: undefined },
    });

    expect(result).toMatchObject({ enabled: true, injectionBudget: 1100, adjudication: 'confirm' });
});

it('uses an existing chat preset binding over later global and character defaults', () => {
    const result = resolveConfig({
        globalConfig: { rulePresetId: 'global-default' },
        characterConfig: { rulePresetId: 'character-default' },
        chatConfig: { rulePresetId: 'pinned-chat-preset', presetVersion: 3 },
    });

    expect(result).toMatchObject({ rulePresetId: 'pinned-chat-preset', presetVersion: 3 });
});

it('returns an immutable resolved configuration', () => {
    const result = resolveConfig({ globalConfig: { enabled: true } });

    expect(Object.isFrozen(result)).toBe(true);
    expect(() => {
        result.enabled = false;
    }).toThrow(TypeError);
    expect(result.enabled).toBe(true);
});
