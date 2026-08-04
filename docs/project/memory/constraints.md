# Constraints

Hard project rules. IDs: `C001`, `C002`, ...

Do not edit manually. Use:

```bash
python .harness/scripts/memory.py add --text "<instruction>"
python .harness/scripts/memory.py relevant --path <repo-relative-file>
```

## C001 - Overview directory is fixed

- Type: constraint
- Scope: `docs/project/overview/`
- Tags: documentation, overview, harness
- Status: active
- Added: 2026-06-02

Rule:
`docs/project/overview/` may contain only `instructions.md`, `project.md`, `goal.md`. No other files/subdirs.

Rationale:
Overview = bootstrap trio: fixed instructions, harness-upgradable `project.md`, LLM-filled `goal.md`. Extra files scatter entry points and break init expectations.

Applies when:
- Adding/moving docs under `docs/project/overview/`
- Scaffolding project docs from harness/agents

Do not:
- Create sibling files under `docs/project/overview/`, e.g. extra Markdown/config.
- Add subdirs under `docs/project/overview/`.
- Put module docs in `overview/`; use `docs/project/modules/`.
- Put memory rules in `overview/`; use `docs/project/memory/`.

## C002 - Ideas are on-request only

- Type: constraint
- Scope: `docs/ideas/`
- Tags: documentation, ideas, harness
- Status: active

Rule:
Do not read/cite `docs/ideas/` unless user explicitly asks for a specific idea file.

Rationale:
Ideas are early-stage, non-authoritative until promoted via `harness-idea promote` into module docs or `memory.py add`.
