const toSnake = (str) => str
    // replace any sequence of non-alphanumeric characters with a single underscore
    .replace(/[^0-9A-Za-z]+/g, '_')
    // insert underscore between a lower-case letter/digit and an upper-case letter (but not between consecutive uppers)
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    // collapse multiple underscores
    .replace(/_+/g, '_')
    // trim leading/trailing underscores
    .replace(/^_+|_+$/g, '')
    // finally, lowercase the result
    .toLowerCase();

export const safeToSnake = (str) => {
    const res = toSnake(str);
    return (res.length >= 2) ? res : str; // considering element with one symbol is too short to be safe
};
