#!/usr/bin/env bash
# Check the deny list, the OpenCode map, and the rendered opencode.json.
# No container. bash must not be denied.
set -euo pipefail
cd "$(dirname "$0")/../.."

render=harness/opencode/render-config.sh
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

"$render" harness/deny harness/opencode/map "$tmp/opencode.json"
diff -u harness/opencode/opencode.json "$tmp/opencode.json"

python3 - "$tmp/opencode.json" <<'PY'
import json, sys
from pathlib import Path

doc = json.load(open(sys.argv[1]))
assert list(doc) == ["$schema", "permission"], list(doc)
assert doc["$schema"] == "https://opencode.ai/config.json"
assert doc["permission"] == {"websearch": "deny", "webfetch": "deny"}
assert "bash" not in doc["permission"]

deny = [ln.strip() for ln in Path("harness/deny").read_text().splitlines() if ln.strip()]
assert deny == ["search", "fetch"], deny
maps = [ln.split() for ln in Path("harness/opencode/map").read_text().splitlines() if ln.strip()]
assert maps == [["search", "websearch"], ["fetch", "webfetch"]], maps
PY

printf 'search\n' > "$tmp/deny"
printf 'search bash\n' > "$tmp/map"
if "$render" "$tmp/deny" "$tmp/map" "$tmp/bad.json" >"$tmp/out" 2>"$tmp/err"; then
  echo "renderer allowed a bash deny" >&2
  exit 1
fi
grep -q bash "$tmp/err"

printf 'search\nmissing\n' > "$tmp/deny"
printf 'search websearch\n' > "$tmp/map"
if "$render" "$tmp/deny" "$tmp/map" "$tmp/bad.json" >"$tmp/out" 2>"$tmp/err"; then
  echo "renderer allowed an unmapped deny name" >&2
  exit 1
fi
grep -q "no map for missing" "$tmp/err"

echo "prove-config ok"
