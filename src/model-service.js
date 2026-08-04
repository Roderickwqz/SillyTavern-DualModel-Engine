import { buildRecorderMessages, buildSummaryMessages } from './prompts/recorder.js';

function abortError() {
    return new DOMException('Recorder request aborted', 'AbortError');
}

function throwIfAborted(signal) {
    if (signal?.aborted) throw abortError();
}

function readableText(value) {
    try {
        return typeof value === 'string' && value.trim() ? value.trim() : String(value);
    } catch {
        return 'Unknown validation error';
    }
}

function normalizeErrors(errors, fallback = 'Validation failed') {
    const source = Array.isArray(errors) ? errors : errors === undefined ? [] : [errors];
    const normalized = source.map((error) => ({
        message: readableText(error?.message ?? error) || fallback,
    }));
    return normalized.length ? normalized : [{ message: fallback }];
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
    if (!result || typeof result !== 'object' || Array.isArray(result) || typeof result.ok !== 'boolean') {
        return { ok: false, errors: [{ message: 'Invalid validator result' }] };
    }
    return result.ok
        ? { ok: true, errors: [] }
        : { ok: false, errors: normalizeErrors(result.errors) };
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

            let value;
            try {
                value = extractJsonObject(response?.content);
            } catch (error) {
                errors = normalizeErrors(error, 'Invalid JSON response');
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
