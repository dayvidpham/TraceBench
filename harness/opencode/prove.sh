#!/usr/bin/env bash
# Run the OpenCode harness as the Harbor agent stage on peasant-smoke.
# The image is tasks/peasant-smoke/environment. Harbor applies no-network
# for the agent and verifier phases. This script does not build an image
# and does not call podman itself.
set -euo pipefail
cd "$(dirname "$0")/../.."

payload=harness/opencode
ref=$(go run "$payload/config-value.go" harness/config.json version)

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

bare=${ref#v}
go run harness/opencode/check-stage.go "$bare"

echo "prove ok ref=$ref"
