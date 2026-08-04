import { expect, it } from 'vitest';
import { createChatTaskQueue } from '../../src/task-queue.js';

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

it('runs tasks serially within a chat while different chats run concurrently', async () => {
    const queue = createChatTaskQueue();
    const firstGate = deferred();
    const otherGate = deferred();
    const order = [];
    const first = queue.enqueue('a', 'r1', async () => {
        order.push('a1-start');
        await firstGate.promise;
        order.push('a1-end');
    });
    const second = queue.enqueue('a', 'r2', async () => { order.push('a2'); });
    const other = queue.enqueue('b', 'r3', async () => {
        order.push('b1-start');
        await otherGate.promise;
        order.push('b1-end');
    });

    await Promise.resolve();
    expect(order).toEqual(['a1-start', 'b1-start']);
    firstGate.resolve();
    await first;
    await second;
    expect(order).toEqual(['a1-start', 'b1-start', 'a1-end', 'a2']);
    otherGate.resolve();
    await other;
});

it('continues after a rejection without poisoning the chat tail', async () => {
    const queue = createChatTaskQueue();
    const failed = queue.enqueue('a', 'r1', async () => { throw new Error('failed'); });
    const failedIdle = queue.waitForIdle('a');
    const recovered = queue.enqueue('a', 'r2', async () => 'recovered');

    await expect(failed).rejects.toThrow('failed');
    await expect(failedIdle).resolves.toBeUndefined();
    await expect(recovered).resolves.toBe('recovered');
    await expect(queue.waitForIdle('a')).resolves.toBe('recovered');
});

it('cancels a running task cooperatively and never calls queued tasks for that chat', async () => {
    const queue = createChatTaskQueue();
    const runningGate = deferred();
    let queuedCalls = 0;
    const running = queue.enqueue('a', 'r1', async signal => {
        await runningGate.promise;
        signal.throwIfAborted();
    });
    const queued = queue.enqueue('a', 'r2', async () => { queuedCalls += 1; });

    await Promise.resolve();
    queue.cancelChat('a', 'stop');
    runningGate.resolve();
    await expect(running).rejects.toMatchObject({ name: 'AbortError', message: 'stop' });
    await expect(queued).rejects.toMatchObject({ name: 'AbortError', message: 'stop' });
    expect(queuedCalls).toBe(0);
});

it('isolates cancellation to the selected chat', async () => {
    const queue = createChatTaskQueue();
    const aGate = deferred();
    const bGate = deferred();
    const a = queue.enqueue('a', 'r1', async signal => {
        await aGate.promise;
        signal.throwIfAborted();
    });
    const b = queue.enqueue('b', 'r2', async signal => {
        await bGate.promise;
        signal.throwIfAborted();
        return 'b-complete';
    });

    await Promise.resolve();
    queue.cancelChat('a');
    aGate.resolve();
    bGate.resolve();
    await expect(a).rejects.toMatchObject({ name: 'AbortError' });
    await expect(b).resolves.toBe('b-complete');
});

it('reports cloned current status and contains observer errors', async () => {
    const statuses = [];
    const gate = deferred();
    const queue = createChatTaskQueue({ onStatus(chatId, status) {
        statuses.push([chatId, structuredClone(status)]);
        throw new Error('observer failure');
    } });
    const task = queue.enqueue('a', 'r1', async () => gate.promise);

    await Promise.resolve();
    const active = queue.getStatus('a');
    expect(active).toEqual({ state: 'pending', requestId: 'r1' });
    active.requestId = 'mutated';
    expect(queue.getStatus('a')).toEqual({ state: 'pending', requestId: 'r1' });
    gate.resolve('done');
    await expect(task).resolves.toBe('done');
    expect(queue.getStatus('a')).toEqual({ state: 'idle', requestId: null });
    expect(statuses).toEqual([
        ['a', { state: 'pending', requestId: 'r1' }],
        ['a', { state: 'idle', requestId: null }],
    ]);
});

it('does not let observer errors hide task failures', async () => {
    const queue = createChatTaskQueue({ onStatus: () => { throw new Error('observer failure'); } });
    await expect(queue.enqueue('a', 'r1', async () => { throw new Error('task failure'); })).rejects.toThrow('task failure');
});

it('waitForIdle waits for the tail captured at call time and resolves after failures', async () => {
    const queue = createChatTaskQueue();
    const gate = deferred();
    const first = queue.enqueue('a', 'r1', async () => gate.promise);
    const idle = queue.waitForIdle('a');
    const second = queue.enqueue('a', 'r2', async () => 'second');

    gate.resolve('first');
    await expect(idle).resolves.toBe('first');
    await expect(first).resolves.toBe('first');
    await expect(second).resolves.toBe('second');
});

it('disposes permanently, aborts known work, and prevents queued callbacks from starting', async () => {
    const queue = createChatTaskQueue();
    const gate = deferred();
    let queuedCalls = 0;
    const running = queue.enqueue('a', 'r1', async signal => {
        await gate.promise;
        signal.throwIfAborted();
    });
    const queued = queue.enqueue('a', 'r2', async () => { queuedCalls += 1; });
    const idle = queue.waitForIdle('a');

    await Promise.resolve();
    queue.dispose();
    gate.resolve();
    await expect(running).rejects.toMatchObject({ name: 'AbortError', message: 'disposed' });
    await expect(queued).rejects.toMatchObject({ name: 'AbortError', message: 'disposed' });
    await expect(idle).resolves.toBeUndefined();
    expect(queuedCalls).toBe(0);
    expect(() => queue.enqueue('a', 'r3', async () => {})).toThrow('Task queue is disposed');
});
