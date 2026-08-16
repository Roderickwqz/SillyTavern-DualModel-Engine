import { expect, test } from '@playwright/test';

test('health and scripted chat return tracker json', async ({ request }) => {
    const health = await request.get('/health');
    expect(health.ok()).toBeTruthy();
    const body = await health.json();
    expect(body.status).toBe('ok');

    const chat = await request.post('/v1/chat/completions', {
        data: {
            campaign_id: 'playwright-c1',
            messages: [{ role: 'user', content: '你好' }],
        },
    });
    expect(chat.ok()).toBeTruthy();
    const payload = await chat.json();
    expect(payload.choices[0].message.content).toContain('```json');
});