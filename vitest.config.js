import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const stub = (name) => path.join(
    __dirname, 'tests', 'rpg-companion-compat', 'stubs', name,
);

// Vendored upstream imports SillyTavern host modules (extensions.js, script.js)
// by relative path; those only exist inside a real SillyTavern install.
const hostModuleStub = {
    name: 'stub-sillytavern-host-modules',
    enforce: 'pre',
    resolveId(source) {
        if (/\/extensions\.js$/.test(source)) {
            return stub('extensions.js');
        }
        if (/\/script\.js$/.test(source)) {
            return stub('script.js');
        }
        return null;
    },
};

export default defineConfig({
    plugins: [hostModuleStub],
    test: {
        environment: 'jsdom',
        restoreMocks: true,
        clearMocks: true,
        include: [
            'tests/{unit,integration}/**/*.test.js',
            'tests/rpg-companion-compat/**/*.test.js',
        ],
    },
});
