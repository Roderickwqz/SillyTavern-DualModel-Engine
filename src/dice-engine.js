const UINT32_RANGE = 2 ** 32;
const MAX_REJECTIONS = 10000;

function nextValue(nextUint32) {
    if (typeof nextUint32 !== 'function') throw new TypeError('Random source must be a function');
    const value = nextUint32();
    if (!Number.isInteger(value) || value < 0 || value >= UINT32_RANGE) throw new TypeError('Random source must return a uint32');
    return value;
}

export function createWebCryptoUint32(cryptoObject = crypto) {
    if (typeof cryptoObject?.getRandomValues !== 'function') throw new Error('Web Crypto is unavailable');
    return () => cryptoObject.getRandomValues(new Uint32Array(1))[0];
}

export function rollDie(sides, nextUint32) {
    if (!Number.isInteger(sides) || sides < 2 || sides > 1000) throw new Error('Dice sides must be an integer from 2 to 1000');
    const limit = Math.floor(UINT32_RANGE / sides) * sides;
    for (let attempts = 0; attempts < MAX_REJECTIONS; attempts += 1) {
        const value = nextValue(nextUint32);
        if (value < limit) return (value % sides) + 1;
    }
    throw new Error('Random source rejected too many values');
}

export function rollExpression(expression, nextUint32) {
    const match = /^([1-9]\d?|100)d([2-9]|[1-9]\d{1,2}|1000)([+-]\d{1,3})?$/.exec(expression);
    if (!match) throw new Error('Invalid dice expression');
    const count = Number(match[1]);
    const sides = Number(match[2]);
    const modifier = Number(match[3] ?? 0);
    const rolls = Array.from({ length: count }, () => rollDie(sides, nextUint32));
    return { rolls, modifier, total: rolls.reduce((sum, roll) => sum + roll, modifier) };
}
