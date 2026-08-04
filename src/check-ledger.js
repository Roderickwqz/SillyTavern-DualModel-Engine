function deepFreeze(value, seen = new WeakSet()) {
    if (!value || typeof value !== 'object' || seen.has(value)) return value;
    seen.add(value);
    for (const child of Object.values(value)) deepFreeze(child, seen);
    return Object.freeze(value);
}

export function createCheckLedger({ makeId, now, initialRecords = [] }) {
    const records = initialRecords.map(record => deepFreeze(structuredClone(record)));
    const freezeRecord = input => deepFreeze(structuredClone({ checkId: makeId(), createdAt: now(), supersedes: null, ...input }));
    return {
        createRecord: freezeRecord,
        reroll: (previous, input) => freezeRecord({ ...input, supersedes: previous.checkId }),
        commit(staged = []) { for (const record of staged) if (record?.checkId && !records.some(item => item.checkId === record.checkId)) records.push(deepFreeze(structuredClone(record))); },
        findReusable({ baseBranchId, signature }) { return records.findLast(record => record.branchId === baseBranchId && record.signature === signature) ?? null; },
        list: () => records.map(record => structuredClone(record)),
    };
}
