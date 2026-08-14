import { describe, expect, it } from 'vitest';
import { stripTrackerJsonBlocks } from '../../../extensions/rpg-companion-compat/src/compat/trackerCleaning.js';

describe('stripTrackerJsonBlocks', () => {
    it('removes tracker-shaped json fences only', () => {
        const input = [
            '故事继续。',
            '```json',
            '{"rules":{"mode":"narrative"},"userStats":{"attributes":[]},"infoBox":{}}',
            '```',
            '代码示例：',
            '```json',
            '{"snippet":"keep me"}',
            '```',
        ].join('\n');
        const output = stripTrackerJsonBlocks(input);
        expect(output).toContain('故事继续。');
        expect(output).toContain('"snippet":"keep me"');
        expect(output).not.toContain('"userStats"');
    });
});
