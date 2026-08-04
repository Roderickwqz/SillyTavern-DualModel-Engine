import js from '@eslint/js';

const browserGlobals = {
    AbortController: 'readonly', DOMException: 'readonly', TextEncoder: 'readonly',
    console: 'readonly', crypto: 'readonly', document: 'readonly', structuredClone: 'readonly',
    window: 'readonly',
};
const nodeGlobals = { Buffer: 'readonly', console: 'readonly', process: 'readonly' };

export default [
    { ignores: ['dist/**', 'node_modules/**'] },
    js.configs.recommended,
    { files: ['src/**/*.js'], languageOptions: { ecmaVersion: 2024, sourceType: 'module', globals: browserGlobals } },
    { files: ['*.config.js', 'tests/**/*.js'], languageOptions: { ecmaVersion: 2024, sourceType: 'module', globals: { ...browserGlobals, ...nodeGlobals } } },
    { rules: { 'no-unused-vars': ['error', { argsIgnorePattern: '^_' }] } },
];
