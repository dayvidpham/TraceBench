#!/usr/bin/env bash
# Run the OpenCode harness as the Harbor agent stage on peasant-smoke.
# The image is tasks/peasant-smoke/environment. Harbor applies no-network
# for the agent and verifier phases. This script does not build an image
# and does not call podman itself.
set -euo pipefail
cd "$(dirname "$0")/../.."

payload=harness/opencode
ref=$(tr -d '[:space:]' < "$payload/version")

test -n "$ref"
test -d "$payload/src"
test -f "$payload/REVISION"
test -x "$payload/bin/opencode"
test -z "$(find "$payload/src" -name .git -print -quit)"
"$payload/render-config.sh"
test -f "$payload/opencode.json"

if command -v harbor >/dev/null 2>&1; then
  harbor=(harbor)
else
  harbor=(python3 -m harbor.cli.main)
fi

export PYTHONPATH="."
"${harbor[@]}" run -p tasks/peasant-smoke \
  -a harness.opencode.agent:OpenCode -e podman

python3 - "$ref" <<'PY'
import sys
from pathlib import Path

ref = sys.argv[1].removeprefix("v")
notes = sorted(Path("jobs").rglob("opencode-stage.txt"), key=lambda p: p.stat().st_mtime)
if not notes:
    sys.exit("no opencode-stage.txt under jobs/")
text = notes[-1].read_text()
print(notes[-1])
print(text, end="")
need = [
    f"version: {ref}",
    "websearch: deny",
    "webfetch: deny",
    "bash: absent",
    "connect: failed",
]
missing = [line for line in need if line not in text]
if missing:
    sys.exit("agent stage log missing: " + ", ".join(missing))
PY

echo "prove ok ref=$ref"
