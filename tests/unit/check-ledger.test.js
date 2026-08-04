import { expect, it } from 'vitest';
import { createCheckLedger } from '../../src/check-ledger.js';

function ids(...values) { return () => values.shift(); }

it('reuses committed records, preserves reroll lineage, and never exposes mutable records', () => {
    const ledger = createCheckLedger({ makeId: ids('c1', 'c2'), now: () => '2026-08-03T00:00:00.000Z' });
    const first = ledger.createRecord({ kind: 'check', branchId: 'b1', signature: 'pick-lock', result: { total: 18 } });
    expect(ledger.findReusable({ baseBranchId: 'b1', signature: 'pick-lock' })).toBeNull();
    ledger.commit([first, first]);
    expect(ledger.findReusable({ baseBranchId: 'b1', signature: 'pick-lock' })).toEqual(first);
    const second = ledger.reroll(first, { kind: 'check', branchId: 'b2', signature: 'pick-lock', result: { total: 7 } });
    ledger.commit([second]);
    expect(second.supersedes).toBe('c1');
    expect(() => { first.result.total = 99; }).toThrow();
    const listed = ledger.list(); listed[0].result.total = 1;
    expect(ledger.list()[0].result.total).toBe(18);
});

it('hydrates persisted records without changing identities', () => {
    const persisted = { checkId: 'saved-c1', createdAt: '2026-08-02T00:00:00.000Z', supersedes: null, kind: 'check', branchId: 'b1', signature: 'pick-lock', result: { total: 18 } };
    const ledger = createCheckLedger({ makeId: () => 'unused', now: () => 'unused', initialRecords: [persisted] });
    persisted.result.total = 0;
    expect(ledger.findReusable({ baseBranchId: 'b1', signature: 'pick-lock' })).toMatchObject({ checkId: 'saved-c1', result: { total: 18 } });
});
