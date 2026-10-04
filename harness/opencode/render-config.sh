#!/usr/bin/env bash
# Write generated opencode.json from harness/config.json and the module map.
# config.json is the single external config (version plus search/fetch toggles).
# tool-map.json is the per-module mapping schema (harness name -> OpenCode key).
# bash is never representable: neither file may mention it.
set -euo pipefail
cd "$(dirname "$0")/../.."

config=${1:-harness/config.json}
map=${2:-harness/opencode/tool-map.json}
out=${3:-harness/opencode/opencode.json}

go run harness/opencode/render.go "$config" "$map" "$out"
