import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
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

    it('loads its bundled Ajv export without dynamic evaluation', async () => {
        const originalFunction = globalThis.Function;
        globalThis.Function = function blockedDynamicEvaluation() {
            throw new Error('dynamic evaluation is blocked by CSP');
        };

        let extension;
        try {
            const entryPointUrl = pathToFileURL(resolve('dist/index.js'));
            extension = await import(`${entryPointUrl.href}?csp=${Date.now()}`);
        } finally {
            globalThis.Function = originalFunction;
        }

        expect(typeof extension.Ajv).toBe('function');
        const ajv = new extension.Ajv();
        expect(ajv.validate({ type: 'string' }, 'ready')).toBe(true);
    });
});
