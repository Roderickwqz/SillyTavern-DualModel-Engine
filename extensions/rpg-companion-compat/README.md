# RPG Companion Compat (LangGraph)

Install via SillyTavern Extensions → Install from URL:

`https://github.com/<org>/SillyTavern-DualModel-Engine/tree/main/extensions/rpg-companion-compat`

## SillyTavern setup

1. Disable the legacy DualModel Engine extension.
2. Chat Completion → Custom OpenAI-compatible:
   - Base URL: `http://127.0.0.1:8000/v1`
   - Custom Body: `{"campaign_id":"campaign-001"}`
3. Enable this extension. Generation mode is locked to Together.
4. Start backend: `python -m sillytavern_rpg_engine serve`

## Authoritative state

Tracker values are read-only. Approve inferred changes in chat:

- `确认提案 <id>`
- `拒绝提案 <id>`

## License

AGPL-3.0. See LICENSE and THIRD_PARTY_NOTICES.md.
