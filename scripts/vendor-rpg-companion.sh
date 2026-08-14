#!/usr/bin/env bash
# scripts/vendor-rpg-companion.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/extensions/rpg-companion-compat"
UPSTREAM_REPO="https://github.com/SpicyMarinara/rpg-companion-sillytavern.git"
UPSTREAM_SHA="e021946867b9a457a3f8b67c78642f9b92ebb90d"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 "$UPSTREAM_REPO" "$TMP/upstream"
git -C "$TMP/upstream" fetch --depth 1 origin "$UPSTREAM_SHA"
git -C "$TMP/upstream" checkout "$UPSTREAM_SHA"
rm -rf "$DEST"
mkdir -p "$DEST"
rsync -a --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  "$TMP/upstream/" "$DEST/"
python3 - <<'PY'
import json, pathlib
root = pathlib.Path("extensions/rpg-companion-compat")
manifest = json.loads((root / "manifest.json").read_text())
manifest["display_name"] = "RPG Companion Compat (LangGraph)"
manifest["version"] = f"{manifest['version']}-compat.1"
(root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
PY
