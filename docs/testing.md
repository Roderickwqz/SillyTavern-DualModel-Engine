# Testing and release verification

Use Node.js `>=20.19.0`. Install exactly the locked dependencies before development or release verification:

```bash
npm ci
```

Run a focused Vitest file while changing one contract:

```bash
npm run test:run -- tests/unit/manifest.test.js
npm run test:run -- tests/unit/state-store.test.js
npm run test:run -- tests/integration/full-flow.test.js tests/integration/save-failure.test.js
```

The regular complete local gate is:

```bash
npm run check
git diff --check
rg -n "https?://|Math\\.random|FastAPI|fetch\\(['\"]https?://" dist/index.js src
npm run build
git diff --exit-code -- dist
```

`npm run check` runs ESLint, every unit/integration Vitest test, builds the bundle, and confirms the generated `dist` matches the committed artifact. The final `npm run build` followed by the `git diff` command proves a second build is reproducible.

## Real-host verification (opt-in)

Automated browser smoke testing requires a locally configured SillyTavern instance; it is not run by default:

```bash
SILLYTAVERN_URL=http://127.0.0.1:8000 npm run test:e2e
```

Then complete [the manual model and proxy checklist](../tests/e2e/manual-model-checklist.md) against the intended SillyTavern release and actual Narrator/Recorder profiles. Record pass/fail and diagnostic evidence for every item, including streaming, tool probing, real tool invocation, fallback, persistence/reload, swipe, continue, delete, edit/recalculate, preset import/export, and raw-data export. A tag requires both this configured-host smoke test and a completed checklist.
