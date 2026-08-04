import { expect, it } from 'vitest';
import { createEmptyEnvelope, migrateEnvelope } from '../../src/migrations.js';

it('creates schema version 1 without conflating the narrative version', () => {
    const value = createEmptyEnvelope({ presetId: 'narrative', initialState: { version: 0, scene: {} } });

    expect(value.schemaVersion).toBe(1);
    expect(value.stateVersion).toBe(0);
    expect(value.headRevision).toBe(0);
    expect(value.initialSnapshot).toEqual({ version: 0, scene: {} });
    expect(migrateEnvelope(value)).toEqual({ ok: true, value });
});

it('clones the initial state so later caller changes cannot affect either snapshot', () => {
    const initialState = { version: 0, scene: { location: 'harbor' } };
    const envelope = createEmptyEnvelope({ presetId: 'narrative', initialState });
    initialState.scene.location = 'market';

    expect(envelope.initialSnapshot.scene.location).toBe('harbor');
    expect(envelope.activeSnapshot.scene.location).toBe('harbor');
    expect(envelope.initialSnapshot).not.toBe(envelope.activeSnapshot);
});

it('clones current schemas instead of sharing mutable snapshots with the input', () => {
    const input = {
        schemaVersion: 1,
        initialSnapshot: { version: 0, scene: { location: 'harbor' } },
        activeSnapshot: { version: 0, scene: { location: 'harbor' } },
    };
    const result = migrateEnvelope(input);
    result.value.activeSnapshot.scene.location = 'market';

    expect(input.activeSnapshot.scene.location).toBe('harbor');
});

it('rejects future schemas without mutating the input', () => {
    const input = { schemaVersion: 99, activeSnapshot: { safe: true } };
    const result = migrateEnvelope(input);

    expect(result.ok).toBe(false);
    expect(input.activeSnapshot.safe).toBe(true);
});
