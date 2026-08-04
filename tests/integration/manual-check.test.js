import { expect, it, vi } from 'vitest';
import { createChatTaskQueue } from '../../src/task-queue.js';

it('serializes a pending Recorder transaction before a manual audit task', async () => {
    const queue = createChatTaskQueue(); let release; const order = []; const recorder = queue.enqueue('chat', 'recorder', async () => { order.push('recorder-start'); await new Promise(resolve => { release = resolve; }); order.push('recorder-end'); });
    const manual = queue.enqueue('chat', 'manual', async signal => { signal.throwIfAborted(); order.push('manual'); });
    await vi.waitFor(() => expect(release).toBeTypeOf('function')); expect(order).toEqual(['recorder-start']);
    release(); await Promise.all([recorder, manual]); expect(order).toEqual(['recorder-start', 'recorder-end', 'manual']);
});

it('cancels a queued manual task without running its side effects', async () => {
    const queue = createChatTaskQueue(); let release; const ran = vi.fn(); const recorder = queue.enqueue('chat', 'recorder', () => new Promise(resolve => { release = resolve; })); const manual = queue.enqueue('chat', 'manual', () => ran());
    await vi.waitFor(() => expect(release).toBeTypeOf('function')); queue.cancelChat('chat'); release(); await expect(manual).rejects.toMatchObject({ name: 'AbortError' }); await recorder.catch(() => undefined); expect(ran).not.toHaveBeenCalled();
});
