#!/usr/bin/env bash
# Check tools.json, the per-module map, and the rendered opencode.json.
# No container. Only search/fetch may be toggled; bash must stay unmapped.
set -euo pipefail
cd "$(dirname "$0")/../.."

render=harness/opencode/render-config.sh
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

"$render" harness/tools.json harness/opencode/tool-map.json "$tmp/opencode.json"
diff -u harness/opencode/opencode.json "$tmp/opencode.json"

go run harness/opencode/check-config.go \
  harness/tools.json harness/opencode/tool-map.json harness/opencode/opencode.json

fail() {
  if "$render" "$@" >"$tmp/out" 2>"$tmp/err"; then
    echo "renderer accepted bad input: $*" >&2
    exit 1
  fi
}

printf '{"search": false, "fetch": false}' > "$tmp/tools.json"
printf '{"search": "bash", "fetch": "webfetch"}' > "$tmp/map.json"
fail "$tmp/tools.json" "$tmp/map.json" "$tmp/bad.json"
grep -q bash "$tmp/err"

printf '{"search": false, "fetch": false, "extra": false}' > "$tmp/tools.json"
printf '{"search": "websearch", "fetch": "webfetch"}' > "$tmp/map.json"
fail "$tmp/tools.json" "$tmp/map.json" "$tmp/bad.json"
grep -q "exactly" "$tmp/err"

printf '{"search": "off", "fetch": false}' > "$tmp/tools.json"
fail "$tmp/tools.json" "$tmp/map.json" "$tmp/bad.json"
grep -q "must be bool" "$tmp/err"

printf '{"search": false}' > "$tmp/tools.json"
fail "$tmp/tools.json" "$tmp/map.json" "$tmp/bad.json"
grep -q "exactly" "$tmp/err"

# search on drops websearch from the rendered permission.
printf '{"search": true, "fetch": false}' > "$tmp/tools.json"
"$render" "$tmp/tools.json" "$tmp/map.json" "$tmp/on.json"
go run harness/opencode/check-config.go --perm-only "$tmp/on.json" '{"webfetch":"deny"}'

echo "prove-config ok"
