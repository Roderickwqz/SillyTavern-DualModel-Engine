import { defineConfig } from 'vitest/config';

export default defineConfig({
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
