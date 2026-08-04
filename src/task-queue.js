function cloneStatus(status) {
    return { ...status };
}

function abortError(reason) {
    return new DOMException(reason, 'AbortError');
}

export function createChatTaskQueue({ onStatus = () => {} } = {}) {
    const entries = new Map();
    let disposed = false;

    function createEntry() {
        return {
            controllers: new Set(),
            queued: [],
            running: null,
            status: { state: 'idle', requestId: null },
            tail: Promise.resolve(),
        };
    }

    function getEntry(chatId) {
        let item = entries.get(chatId);
        if (!item) {
            item = createEntry();
            entries.set(chatId, item);
        }
        return item;
    }

    function report(chatId, entry, status) {
        if (entry.status.state === status.state && entry.status.requestId === status.requestId) return;
        entry.status = status;
        try {
            onStatus(chatId, cloneStatus(status));
        } catch {
            // Status callbacks are diagnostic only.
        }
    }

    function nextStatus(chatId, entry) {
        const next = entry.queued[0];
        report(chatId, entry, next
            ? { state: 'pending', requestId: next.requestId }
            : { state: 'idle', requestId: null });
    }

    function knownEntry(chatId) {
        return entries.get(chatId);
    }

    return {
        enqueue(chatId, requestId, task) {
            if (disposed) throw new Error('Task queue is disposed');
            const entry = getEntry(chatId);
            const controller = new AbortController();
            const item = { controller, requestId };
            entry.controllers.add(controller);
            entry.queued.push(item);

            const run = async () => {
                entry.queued.splice(entry.queued.indexOf(item), 1);
                entry.running = item;
                report(chatId, entry, { state: 'pending', requestId });
                try {
                    controller.signal.throwIfAborted();
                    return await task(controller.signal);
                } finally {
                    entry.controllers.delete(controller);
                    if (entry.running === item) entry.running = null;
                    nextStatus(chatId, entry);
                }
            };
            const result = entry.tail.then(run, run);
            entry.tail = result.catch(() => undefined);
            return result;
        },

        waitForIdle(chatId) {
            return knownEntry(chatId)?.tail ?? Promise.resolve();
        },

        cancelChat(chatId, reason = 'chat-cancelled') {
            const entry = knownEntry(chatId);
            if (!entry) return;
            for (const controller of entry.controllers) controller.abort(abortError(reason));
        },

        getStatus(chatId) {
            return cloneStatus(knownEntry(chatId)?.status ?? { state: 'idle', requestId: null });
        },

        dispose() {
            if (disposed) return;
            disposed = true;
            for (const [chatId] of entries) this.cancelChat(chatId, 'disposed');
        },
    };
}
