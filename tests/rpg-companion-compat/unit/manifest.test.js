import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('RPG Companion compat manifest', () => {
    it('is pinned and renamed for the LangGraph fork', () => {
        const manifest = JSON.parse(
            readFileSync('extensions/rpg-companion-compat/manifest.json', 'utf8'),
        );
        expect(manifest.display_name).toContain('Compat');
        expect(manifest.js).toBe('index.js');
        expect(manifest.version).toMatch(/-compat\./);
    });

    it('records upstream pin in UPSTREAM.md', () => {
        const upstream = readFileSync('extensions/rpg-companion-compat/UPSTREAM.md', 'utf8');
        expect(upstream).toContain('e021946867b9a457a3f8b67c78642f9b92ebb90d');
        expect(upstream).toContain('SpicyMarinara/rpg-companion-sillytavern');
    });
});
