## Conda Env

Use `CONDA_ENV` in `<PROJECT_ROOT>/.env`. `harness-map` / `project.py` upserts if missing; `harness-setup` does not overwrite existing `.env`.

## Repository Layout

Use **codegraph** MCP for symbols, callers, callees, and dependency structure. Refresh the index with `harness-map script` (or `codegraph index`).

Optional CLI helpers: `python .harness/scripts/registry.py`, `python .harness/scripts/dependency.py`; also `harness-map inspect` / `harness-map risk`.

## Module Contracts

Load module design docs via `docs/project/modules/index.json` when needed.

Before code changes in a package area:

```bash
python .harness/scripts/modules.py relevant --path <repo-relative-path>
```

Then read returned `docs/project/modules/<id>.md`.

Do not hand-edit `docs/project/modules/index.json`. Regenerate with `modules.py rebuild-index`.

Create or refresh a module contract:

```bash
python .harness/scripts/modules.py analyze --module <id>
# or
harness-module
```

`harness-map llm` runs module analyze incrementally (skips unchanged modules by default; `--refresh-all` to force).

Memory ↔ modules auto-links:
- After `memory.py add`, matching module docs get `related_memory` + **Related Agent Memory**; `modules/index.json` rebuilds.
- After `modules.py analyze`, `related_memory` is filled from `memory/index.json` by scope overlap.
