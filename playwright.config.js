import { defineConfig } from '@playwright/test';

export default defineConfig({
    testDir: 'tests/e2e',
    use: { baseURL: process.env.SILLYTAVERN_URL ?? 'http://127.0.0.1:8000' },
    webServer: undefined,
});
