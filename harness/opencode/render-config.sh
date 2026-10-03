#!/usr/bin/env bash
# Write opencode.json from harness/deny and harness/opencode/map.
# Deny names are the harness interface. The map names OpenCode's keys.
# bash is never denied. Curl and wget stay allowed.
set -euo pipefail
cd "$(dirname "$0")/../.."

deny=${1:-harness/deny}
map=${2:-harness/opencode/map}
out=${3:-harness/opencode/opencode.json}

python3 - "$deny" "$map" "$out" <<'PY'
import json, sys

deny_path, map_path, out_path = sys.argv[1:]

def rows(path):
    found = []
    for raw in open(path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        found.append(line)
    return found

deny = rows(deny_path)
if not deny:
    sys.exit("deny list is empty")
if len(deny) != len(set(deny)):
    sys.exit("duplicate deny name")

mapping = {}
for line in rows(map_path):
    parts = line.split()
    if len(parts) != 2:
        sys.exit(f"bad map line: {line}")
    src, dst = parts
    if src in mapping:
        sys.exit(f"duplicate map name: {src}")
    if dst == "bash":
        sys.exit("refusing to deny bash")
    mapping[src] = dst

perm = {}
for name in deny:
    if name not in mapping:
        sys.exit(f"no map for {name}")
    tool = mapping[name]
    if tool in perm:
        sys.exit(f"duplicate permission key: {tool}")
    perm[tool] = "deny"

if "bash" in perm:
    sys.exit("refusing to deny bash")

doc = {
    "$schema": "https://opencode.ai/config.json",
    "permission": perm,
}
with open(out_path, "w") as fh:
    json.dump(doc, fh, indent=2)
    fh.write("\n")
PY
