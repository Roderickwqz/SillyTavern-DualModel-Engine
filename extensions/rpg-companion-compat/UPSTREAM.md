# Upstream RPG Companion

- Repository: https://github.com/SpicyMarinara/rpg-companion-sillytavern
- Pinned commit: e021946867b9a457a3f8b67c78642f9b92ebb90d
- Upstream version: 3.7.4
- License: AGPL-3.0

## Refresh procedure

1. Run `bash scripts/vendor-rpg-companion.sh`
2. Re-apply patches listed in `THIRD_PARTY_NOTICES.md`
3. Run `npm run test:run -- tests/rpg-companion-compat`
