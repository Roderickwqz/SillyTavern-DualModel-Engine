// Optional real SillyTavern compat smoke. Skipped unless SILLYTAVERN_URL is set to a
// running local SillyTavern with the RPG Companion Compat extension enabled and the
// LangGraph RPG backend reachable. See tests/e2e/rpg-compat-manual-checklist.md for the
// full release gate.
//
// NOTE: does NOT use the shared baseURL. baseURL may point at the backend webServer
// (RPG_BACKEND_URL, port 8765) or the SillyTavern origin, so every URL is explicit.
import { expect, test } from '@playwright/test';

test.skip(!process.env.SILLYTAVERN_URL, 'Set SILLYTAVERN_URL to run SillyTavern compat smoke');

test('compat extension loads without DualModel errors', async ({ page }) => {
    const url = process.env.SILLYTAVERN_URL;
    const errors = [];
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });

    // Panel is appended to <body> by the extension on init (extension enabled, no chat
    // context required to mount the container), so asserting its visibility is stable.
    await page.goto(url);
    await expect(page.locator('#rpg-companion-panel')).toBeVisible();
    expect(errors.filter(error => error.includes('DualModel'))).toEqual([]);
});

test('backend health reachable', async ({ request }) => {
    const base = process.env.RPG_BACKEND_URL ?? 'http://127.0.0.1:8000';
    expect((await request.get(`${base}/health`)).ok()).toBeTruthy();
});