#!/usr/bin/env bash
# Write opencode.json from harness/tools.json and harness/opencode/tool-map.json.
# tools.json is the single external toggle (only search/fetch, bool on/off).
# tool-map.json is the per-module mapping schema (harness name -> OpenCode key).
# bash is never representable: neither file may mention it.
set -euo pipefail
cd "$(dirname "$0")/../.."

tools=${1:-harness/tools.json}
map=${2:-harness/opencode/tool-map.json}
out=${3:-harness/opencode/opencode.json}

go run harness/opencode/render.go "$tools" "$map" "$out"
