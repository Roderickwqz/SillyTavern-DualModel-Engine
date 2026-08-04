import Ajv from 'ajv';

const validateBootstrapResult = new Ajv().compile({
    type: 'object',
    properties: { name: { const: 'dualModelEngine' } },
    required: ['name'],
    additionalProperties: false,
});

export async function bootstrap() {
    const result = { name: 'dualModelEngine' };

    if (!validateBootstrapResult(result)) {
        throw new Error('DualModel Engine bootstrap result is invalid');
    }

    return result;
}

if (typeof document !== 'undefined') {
    void bootstrap();
}
