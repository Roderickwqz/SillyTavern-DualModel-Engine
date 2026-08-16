import os from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

// Unique DB per run: webServer always spawns a fresh server, no stale files.
const E2E_DB = path.join(os.tmpdir(), `rpg-e2e-${Date.now()}.db`);

export default defineConfig({
    testDir: 'tests/e2e',
    testMatch: '**/*.spec.js',
    timeout: 30_000,
    webServer: {
        command: `RPG_ENGINE_TEST_MODE=scripted python -m sillytavern_rpg_engine serve --database ${E2E_DB} --port 8765`,
        // Readiness via stderr line instead of a url probe: on this host a TCP
        // connect to a not-yet-listening port is dropped (not refused), which
        // would hang the pre-spawn url availability check forever.
        wait: { stderr: /Application startup complete/ },
    },
    projects: [
        {
            name: 'extension-smoke',
            testMatch: /extension-smoke\.spec\.js/,
            use: { baseURL: process.env.SILLYTAVERN_URL ?? 'http://127.0.0.1:8000' },
        },
        {
            name: 'backend-api',
            testMatch: /backend-api\.spec\.js/,
            use: { baseURL: process.env.RPG_BACKEND_URL ?? 'http://127.0.0.1:8765' },
        },
    ],
});