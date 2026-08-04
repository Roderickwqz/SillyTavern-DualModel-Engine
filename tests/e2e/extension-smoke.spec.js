import { expect, test } from '@playwright/test';

test.skip(!process.env.SILLYTAVERN_URL, 'Set SILLYTAVERN_URL to a running local SillyTavern with the extension installed');

test('loads the extension and exposes diagnostics without DualModel console errors', async ({ page }) => {
    const errors = [];
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.goto('/');
    await expect(page.locator('#dualmodel-settings')).toBeVisible();
    await expect(page.locator('[data-dme-action="probe-tools"]')).toBeEnabled();
    expect(errors.filter(error => error.includes('DualModel'))).toEqual([]);
});
