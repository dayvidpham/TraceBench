#!/bin/bash
# Acceptance check for issue #1 (containerize the test-case codebase).
# Verifies, using Podman as the default local provider:
#   V1  image builds and the oracle trial passes (reward 1.0)
#   V2  the task codebase starts from the image (pinned SHA, hashes match)
#   V3  the host filesystem stays unchanged (git status, sentinel, file hashes)
#   V4  repeatability (V1+V3 a second time; image layers are cached so it is fast)
#
# Usage: ./scripts/verify-issue-1.sh [--repeat]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASK="tasks/peasant-smoke"
PINNED_SHA="4153b0026c9e73157ef663368a7bb497c022afc1"

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; exit 1; }

run_oracle() {
  local out
  out="$(harbor run -p "$TASK" -a oracle -e podman 2>&1 | tail -20)"
  local jobdir
  jobdir="$(echo "$out" | grep -o 'jobs/[0-9_-]*' | head -1)"
  [ -n "${jobdir:-}" ] || fail "no job dir in harbor output"
  python3 - "$REPO_ROOT/$jobdir/result.json" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
stats = r["stats"]["evals"]
means = [m["mean"] for e in stats.values() for m in e["metrics"]]
assert means == [1.0], f"expected reward 1.0, got {means}"
EOF
  echo "$jobdir"
}

find_task_image() {
  # The task image is the one carrying /peasant.
  podman images --format "{{.Id}}" | while read -r id; do
    if podman run --rm "$id" test -d /peasant 2>/dev/null; then
      echo "$id"
      break
    fi
  done
}

cd "$REPO_ROOT"

echo "--- V1: oracle trial (reward 1.0) ---"
JOBDIR="$(run_oracle)"
pass "V1 oracle reward 1.0 ($JOBDIR)"

echo "--- V2: codebase starts from the image ---"
IMG="$(find_task_image)"
[ -n "${IMG:-}" ] || fail "no image with /peasant found"
SHA="$(podman run --rm "$IMG" git -C /peasant rev-parse HEAD)"
[ "$SHA" = "$PINNED_SHA" ] || fail "image SHA $SHA != pinned $PINNED_SHA"
# Working tree must match the committed blob at the pinned SHA (no tampering
# between clone and build).
TREE_HASH="$(podman run --rm "$IMG" sha256sum /peasant/go.mod | cut -d' ' -f1)"
BLOB_HASH="$(podman run --rm "$IMG" git -C /peasant show HEAD:go.mod | sha256sum | cut -d' ' -f1)"
[ "$TREE_HASH" = "$BLOB_HASH" ] || fail "working tree go.mod != HEAD blob"
pass "V2 image at pinned SHA $SHA (tree matches blob)"

echo "--- V3: host filesystem unchanged ---"
git status --porcelain > /tmp/tb-v3-before.txt
SENTINEL="$(openssl rand -hex 16)"
echo "$SENTINEL" > /tmp/tb-host-sentinel
JOBDIR2="$(run_oracle)"
[ "$(cat /tmp/tb-host-sentinel)" = "$SENTINEL" ] || fail "sentinel changed"
git status --porcelain > /tmp/tb-v3-after.txt
diff /tmp/tb-v3-before.txt /tmp/tb-v3-after.txt > /dev/null \
  || fail "git status changed during run"
[ ! -e /host_pwned ] || fail "stray /host_pwned on host"
pass "V3 host unchanged ($JOBDIR2)"

if [ "${1:-}" = "--repeat" ]; then
  echo "--- V4: repeat V1+V3 ---"
  run_oracle > /dev/null
  git status --porcelain > /tmp/tb-v4.txt
  diff /tmp/tb-v3-after.txt /tmp/tb-v4.txt > /dev/null \
    || fail "git status changed on repeat run"
  pass "V4 repeat run clean"
fi

echo "ALL CHECKS PASSED"
