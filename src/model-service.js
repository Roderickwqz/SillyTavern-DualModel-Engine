import { buildRecorderMessages, buildSummaryMessages } from './prompts/recorder.js';

function abortError() {
    return new DOMException('Recorder request aborted', 'AbortError');
}

function throwIfAborted(signal) {
    if (signal?.aborted) throw abortError();
}

function normalizeErrors(errors, fallback = 'Validation failed') {
    return errors.map((error) => {
        let message = '';
        try {
            if (typeof error === 'string') message = error;
            else if (error && typeof error === 'object' && typeof error.message === 'string') message = error.message;
        } catch {
            message = '';
        }
        return { message: message.trim() || fallback };
    });
}

function formatErrors(errors) {
    return errors.map((error) => error.message).join('; ');
}

export function extractJsonObject(text) {
    const trimmed = String(text).trim();
    const fenced = trimmed.match(/^```(?:json)?\s*\n?([\s\S]*?)\n?```$/i);
    const value = JSON.parse(fenced ? fenced[1].trim() : trimmed);
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError('Response must be a JSON object');
    }
    return value;
}

function validate(value, validateValue, input) {
    const result = validateValue(value, input);
    if (!result || typeof result !== 'object' || Array.isArray(result) || typeof result.ok !== 'boolean' || !Array.isArray(result.errors)) {
        throw new TypeError('Invalid validator result');
    }
    for (let index = 0; index < result.errors.length; index += 1) {
        if (!Object.hasOwn(result.errors, index)) throw new TypeError('Invalid validator result');
    }
    if ((result.ok && result.errors.length !== 0) || (!result.ok && result.errors.length === 0)) {
        throw new TypeError('Invalid validator result');
    }
    return result.ok ? { ok: true, errors: [] } : { ok: false, errors: normalizeErrors(result.errors) };
}

export function createModelService({ adapter, validatePatch, validateState }) {
    async function requestValidated(input, buildMessages, validateValue, resultKey, label) {
        let errors = [];

        for (let attempt = 0; attempt < 2; attempt += 1) {
            throwIfAborted(input.signal);
            const messages = buildMessages({ ...input, validationErrors: errors });
            let response;
            try {
                response = await adapter.requestProfile(input.profileId, messages, 1200, {
                    extractData: true,
                    includePreset: true,
                    stream: false,
                    signal: input.signal,
                }, {});
            } catch (error) {
                if (input.signal?.aborted) throw abortError();
                throw error;
            }
            throwIfAborted(input.signal);

            let content;
            try {
                content = response.content;
            } catch (error) {
                if (input.signal?.aborted) throw abortError();
                throw error;
            }
            let value;
            try {
                value = extractJsonObject(content);
            } catch {
                errors = [{ message: 'Response was not a valid JSON object' }];
                continue;
            }

            throwIfAborted(input.signal);
            let result;
            try {
                result = validate(value, validateValue, input);
            } catch (error) {
                if (input.signal?.aborted) throw abortError();
                throw error;
            }
            throwIfAborted(input.signal);
            if (result.ok) return { [resultKey]: value, repaired: attempt === 1 };
            errors = result.errors;
        }

        throw new Error(`Invalid ${label} response: ${formatErrors(errors)}`);
    }

    return {
        requestPatch: input => requestValidated(input, buildRecorderMessages, validatePatch, 'patch', 'Recorder'),
        requestSummary: input => requestValidated(input, buildSummaryMessages, validateState, 'state', 'summary'),
    };
}
