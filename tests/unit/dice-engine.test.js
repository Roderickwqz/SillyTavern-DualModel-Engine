import { describe, expect, it } from 'vitest';
import { createWebCryptoUint32, rollDie, rollExpression } from '../../src/dice-engine.js';

describe('dice engine', () => {
    it('rejects uint32 values outside an unbiased d20 range', () => {
        const values = [4294967295, 13];
        expect(rollDie(20, () => values.shift())).toBe(14);
    });

    it('parses bounded damage expressions', () => {
        const values = [0, 5];
        expect(rollExpression('2d6+3', () => values.shift())).toEqual({ rolls: [1, 6], modifier: 3, total: 10 });
    });

    it.each(['0d6', '101d6', '1d1', '1d1001', '1d6+1000', '01d6', '1d6x2', 42])('rejects unbounded or malformed expressions: %s', (expression) => {
        expect(() => rollExpression(expression, () => 0)).toThrow('Invalid dice expression');
    });

    it('rejects invalid dice sources and stops an endless rejection stream', () => {
        expect(() => rollDie(20, () => -1)).toThrow('Random source must return a uint32');
        expect(() => rollDie(20, () => 1.5)).toThrow('Random source must return a uint32');
        expect(() => rollDie(20, () => 4294967295)).toThrow('Random source rejected too many values');
    });

    it('refuses production dice when Web Crypto is unavailable', () => {
        const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
        try {
            delete globalThis.crypto;
            expect(() => createWebCryptoUint32()).toThrow('Web Crypto is unavailable');
        } finally {
            if (original) Object.defineProperty(globalThis, 'crypto', original);
        }
    });

    it('reads uint32 values only from Web Crypto', () => {
        const words = [];
        const next = createWebCryptoUint32({ getRandomValues: (array) => { array[0] = 123; words.push(array); return array; } });
        expect(next()).toBe(123);
        expect(words[0]).toBeInstanceOf(Uint32Array);
    });

    it('uses globalThis.crypto by default without leaking a test replacement', () => {
        const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
        const replacement = { getRandomValues: (array) => { array[0] = 321; return array; } };
        try {
            Object.defineProperty(globalThis, 'crypto', { configurable: true, value: replacement });
            expect(createWebCryptoUint32()()).toBe(321);
        } finally {
            if (original) Object.defineProperty(globalThis, 'crypto', original);
            else delete globalThis.crypto;
        }
        expect(globalThis.crypto).not.toBe(replacement);
    });
});
