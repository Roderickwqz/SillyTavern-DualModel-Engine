export { default as Ajv } from 'ajv';
import { probeHostCapabilities } from './capability-probe.js';

export async function bootstrap({ adapter } = {}) {
    const runtimeAdapter = adapter ?? (await import('./st-runtime.js')).createRuntimeAdapter();

    return {
        name: 'dualModelEngine',
        adapter: runtimeAdapter,
        capabilities: probeHostCapabilities(runtimeAdapter),
    };
}

if (typeof document !== 'undefined' && import.meta.url.includes('/scripts/extensions/')) {
    void bootstrap();
}
