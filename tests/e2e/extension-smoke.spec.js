// DEPRECATED. Superseded by tests/e2e/rpg-compat-manual-checklist.md (full release gate)
// and tests/e2e/rpg-compat-smoke.spec.js (optional automated smoke against a real
// SillyTavern + LangGraph backend). Kept for reference only; always skipped.
import { test } from '@playwright/test';

test.skip(true, 'Deprecated: superseded by tests/e2e/rpg-compat-manual-checklist.md');

test('loads the extension and exposes diagnostics without DualModel console errors', async () => {
    // Never runs: module-level skip above points to tests/e2e/rpg-compat-manual-checklist.md.
});
