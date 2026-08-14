const fs = require('fs-extra');
const path = require('path');
const chokidar = require('chokidar');
const glob = require('glob');

const COMPILED_DIR = __dirname // path.join(__dirname, 'compiled');

function findUnlocalizedText() {
    const srcArg = process.argv.find(arg => arg.startsWith('--src='));
    const srcDir = srcArg ? srcArg.split('=')[1] : '.';

    console.log(`\n🔎 Scanning for unlocalized text in ${srcDir}...`);

    const files = glob.sync(`${srcDir}/**/*.{html,js,jsx}`, {
        ignore: ['**/node_modules/**', '**/dist/**', '**/build/**']
    });

    if (files.length === 0) {
        console.log('⚠️  No .html/.js/.jsx files found');
        return;
    }

    let totalFound = 0;

    for (const file of files) {
        const content = fs.readFileSync(file, 'utf8');
        const lines = content.split('\n');
        const relPath = path.relative(process.cwd(), file);

        // Searching for string number
        lines.forEach((line, index) => {
            let match;
            const localPattern = /<([a-zA-Z][a-zA-Z0-9]*)(?:\s(?:[^>](?!data-i18n-key))*)?>([\p{L}\p{N}\s\-.,!?:'"()]+)<\/\1>/gu;

            while ((match = localPattern.exec(line)) !== null) {
                const text = match[2].trim();
                if (!text) continue;

                // Passing JSX expressions like {someVar}
                if (text.includes('{') || text.includes('}')) continue;

                // Passing if tag has data-i18n-key
                if (match[0].includes('data-i18n-key')) continue;

                console.log(`   - ${relPath}:${index + 1} — <${match[1]}> "${text}"`);
                totalFound++;
            }
        });
    }

    if (totalFound === 0) {
        console.log('✅ No unlocalized text found!');
    } else {
        console.log(`\n📋 Found ${totalFound} potentially unlocalized text node(s)`);
    }
}


// Function to validate translations
function validateTranslations() {
    console.log('🔍 Validating translation files...');

    // Parse --locales=en,fr argument
    const localesArg = process.argv.find(arg => arg.startsWith('--locales='));
    const selectedLocales = localesArg
        ? localesArg.split('=')[1].split(',').map(l => l.trim())
        : null;

    const files = fs.readdirSync(COMPILED_DIR)
        .filter(file => file.endsWith('.json'))
        .filter(file => {
            const locale = path.basename(file, '.json');
            return !selectedLocales || selectedLocales.includes(locale);
        });

    if (files.length === 0) {
        console.log('⚠️  No compiled translation files found');
        return;
    }

    // Load all translation data
    const translations = {};
    for (const file of files) {
        const locale = path.basename(file, '.json');
        const filePath = path.join(COMPILED_DIR, file);
        translations[locale] = fs.readJsonSync(filePath);
    }

    // Get all locales
    const locales = Object.keys(translations);
    console.log(`📁 Found ${locales.length} locales: ${locales.join(', ')}`);

    if (locales.length < 2) {
        console.log('⚠️  Need at least 2 locales to compare');
        return;
    }

    // Choose the first locale as reference
    const referenceLocale = locales[0];
    console.log(`🔑 Using ${referenceLocale} as reference locale`);

    // Get all keys from reference locale
    const referenceKeys = Object.keys(translations[referenceLocale]);
    console.log(`🔢 Reference locale has ${referenceKeys.size} unique keys`);

    // Track statistics
    const stats = {
        missingKeys: {},
        extraKeys: {},
        typeErrors: {}
    };

    // Initialize stats for each locale
    for (const locale of locales) {
        if (locale !== referenceLocale) {
            stats.missingKeys[locale] = [];
            stats.extraKeys[locale] = [];
            stats.typeErrors[locale] = [];
        }
    }

    // Check each locale against the reference
    for (const locale of locales) {
        if (locale === referenceLocale) continue;

        const localeKeys = Object.keys(translations[locale]);

        // Check for missing keys
        for (const key of referenceKeys) {
            if (!key in translations[locale]) {
                stats.missingKeys[locale].push(key);
            } else {
                // Check for type mismatches
                const refValue = translations[referenceLocale][key];
                const localeValue = translations[locale][key];

                if (typeof refValue !== typeof localeValue) {
                    stats.typeErrors[locale].push({
                        key,
                        refType: typeof refValue,
                        localeType: typeof localeValue
                    });
                }
            }
        }

        // Check for extra keys
        for (const key of localeKeys) {
            if (!key in translations[referenceLocale]) {
                stats.extraKeys[locale].push(key);
            }
        }
    }

    // Print results
    let hasIssues = false;

    // Print missing keys
    for (const locale in stats.missingKeys) {
        const missing = stats.missingKeys[locale];
        if (missing.length > 0) {
            hasIssues = true;
            console.log(`❌ ${locale} is missing ${missing.length} keys:`);
            missing.forEach(key => {
                console.log(`   - ${key}`);
            });
        }
    }

    // Print extra keys
    for (const locale in stats.extraKeys) {
        const extra = stats.extraKeys[locale];
        if (extra.length > 0) {
            hasIssues = true;
            console.log(`⚠️  ${locale} has ${extra.length} extra keys:`);
            extra.forEach(key => {
                console.log(`   - ${key}`);
            });
        }
    }

    // Print type errors
    for (const locale in stats.typeErrors) {
        const typeErrors = stats.typeErrors[locale];
        if (typeErrors.length > 0) {
            hasIssues = true;
            console.log(`⚠️  ${locale} has ${typeErrors.length} type mismatches:`);
            typeErrors.forEach(err => {
                console.log(`   - ${err.key}: expected ${err.refType}, got ${err.localeType}`);
            });
        }
    }

    // Print empty values check if needed
    console.log('\n📊 Checking for empty values...');
    for (const locale of locales) {
        checkEmptyValues(translations[locale], locale);
    }

    if (!hasIssues) {
        console.log('✅ All locales have consistent structure!');
    }

    return hasIssues;
}

// Function to check for empty values
function checkEmptyValues(obj, locale, prefix = '') {
    for (const key in obj) {
        const fullKey = prefix ? `${prefix}.${key}` : key;
        const value = obj[key];

        if (value === '') {
            console.log(`⚠️  ${locale} has empty string at ${fullKey}`);
        } else if (value === null) {
            console.log(`⚠️  ${locale} has null value at ${fullKey}`);
        } else if (typeof value === 'object' && !Array.isArray(value)) {
            checkEmptyValues(value, locale, fullKey);
        }
    }
}

// Main function
function main() {
    // Create compiled directory if it doesn't exist
    fs.ensureDirSync(COMPILED_DIR);

    // Run validation
    validateTranslations();

    // Find unlocalized text
    findUnlocalizedText();
}

// Watch mode
if (process.argv.includes('--watch')) {
    console.log('👀 Watching for changes...');

    let debounceTimer;
    const debounceDelay = 100;

    // Initial validation
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
        main();
    }, debounceDelay);

    // Watch for changes in the compiled directory
    chokidar.watch(COMPILED_DIR, {
        ignoreInitial: true,
        ignored: /.*~$/, // Игнорировать скрытые файлы
    }).on('all', (event, path) => {
        if (event === 'change' || event === 'add' || event === 'unlink') {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                console.log(`🔁 Detected changes in ${path} (${event}), revalidating...`);
                main();
            }, debounceDelay);
        }
    });
} else {
    // Run once
    main();
}
