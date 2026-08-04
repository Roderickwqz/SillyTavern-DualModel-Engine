import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('extension package', () => {
    it('declares an installable 1.18 extension with committed assets', () => {
        const manifest = JSON.parse(readFileSync('manifest.json', 'utf8'));
        expect(manifest).toMatchObject({
            display_name: 'DualModel Engine',
            js: 'dist/index.js',
            css: 'dist/style.css',
            minimum_client_version: '1.18.0',
        });
        expect(existsSync('dist/index.js')).toBe(true);
        expect(existsSync('dist/style.css')).toBe(true);
    });
});
