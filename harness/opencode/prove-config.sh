#!/usr/bin/env bash
# Check the single parent config, the per-module map, and rendered output.
# No container. Only search/fetch may be toggled; bash must stay unmapped.
set -euo pipefail
cd "$(dirname "$0")/../.."

render=harness/opencode/render-config.sh
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

"$render" harness/config.json harness/opencode/tool-map.json "$tmp/opencode.json"

go run harness/opencode/check-config.go \
  harness/config.json harness/opencode/tool-map.json "$tmp/opencode.json"

fail() {
  if "$render" "$@" >"$tmp/out" 2>"$tmp/err"; then
    echo "renderer accepted bad input: $*" >&2
    exit 1
  fi
}

printf '{"version":"v1.18.34","tools":{"search": false, "fetch": false}}' > "$tmp/config.json"
printf '{"search": "bash", "fetch": "webfetch"}' > "$tmp/map.json"
fail "$tmp/config.json" "$tmp/map.json" "$tmp/bad.json"
grep -q bash "$tmp/err"

printf '{"version":"v1.18.34","tools":{"search": false, "fetch": false, "extra": false}}' > "$tmp/config.json"
printf '{"search": "websearch", "fetch": "webfetch"}' > "$tmp/map.json"
fail "$tmp/config.json" "$tmp/map.json" "$tmp/bad.json"
grep -q "exactly" "$tmp/err"

printf '{"version":"v1.18.34","tools":{"search": "off", "fetch": false}}' > "$tmp/config.json"
fail "$tmp/config.json" "$tmp/map.json" "$tmp/bad.json"
grep -q "must be bool" "$tmp/err"

printf '{"version":"v1.18.34","tools":{"search": false}}' > "$tmp/config.json"
fail "$tmp/config.json" "$tmp/map.json" "$tmp/bad.json"
grep -q "exactly" "$tmp/err"

# search on drops websearch from the rendered permission.
printf '{"version":"v1.18.34","tools":{"search": true, "fetch": false}}' > "$tmp/config.json"
"$render" "$tmp/config.json" "$tmp/map.json" "$tmp/on.json"
go run harness/opencode/check-config.go --perm-only "$tmp/on.json" '{"webfetch":"deny"}'

echo "prove-config ok"
