#!/bin/bash
# Acceptance check for issue #2 (snapshot API, Go implementation).
# S1  go vet + go test (date cutoff, PR exclusivity, determinism, binary client)
# S2  CLI on this repo with a date cutoff: no commit after cutoff in history.json
# S3  determinism: same call twice -> identical manifest_sha256
# S4  PR exclusivity via --pr-start-override: commit at exactly PR start excluded
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT/snapshot"
export PATH="/opt/homebrew/bin:$PATH"

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; exit 1; }

echo "--- S1: go vet + go test ---"
gofmt -l . | grep . && fail "gofmt dirty" || true
go vet ./... || fail "go vet failed"
go test ./... || fail "go test failed"
pass "S1 go test"

echo "--- S2: date cutoff on TraceBench repo ---"
# Pick a cutoff in the middle of this repo's history so the check is nontrivial.
CUTOFF=$(git -C "$REPO_ROOT" log --format=%cI --reverse | sed -n '4p')
[ -n "$CUTOFF" ] || fail "could not derive cutoff"
rm -rf /tmp/tb-snap-s2 && go run ./cmd/snapshot --repo "$REPO_ROOT" \
  --cutoff-type date --cutoff-date "$CUTOFF" --out /tmp/tb-snap-s2 > /dev/null
python3 - "/tmp/tb-snap-s2/history.json" "$CUTOFF" <<'EOF'
import json, sys
from datetime import datetime, timezone
payload = json.load(open(sys.argv[1]))
cutoff = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
bad = [c["sha"] for c in payload["commits"]
       if datetime.fromisoformat(c["committer_time"]) > cutoff]
assert not bad, f"commits after cutoff: {bad}"
assert payload["commits"], "expected non-empty history"
print(f"commits={len(payload['commits'])} files={len(payload['files'])}")
EOF
pass "S2 no commit after $CUTOFF"

echo "--- S3: determinism ---"
rm -rf /tmp/tb-snap-s3a /tmp/tb-snap-s3b
go run ./cmd/snapshot --repo "$REPO_ROOT" --cutoff-type date \
  --cutoff-date "$CUTOFF" --out /tmp/tb-snap-s3a > /dev/null
go run ./cmd/snapshot --repo "$REPO_ROOT" --cutoff-type date \
  --cutoff-date "$CUTOFF" --out /tmp/tb-snap-s3b > /dev/null
HA=$(python3 -c "import json; print(json.load(open('/tmp/tb-snap-s3a/history.json'))['manifest_sha256'])")
HB=$(python3 -c "import json; print(json.load(open('/tmp/tb-snap-s3b/history.json'))['manifest_sha256'])")
[ "$HA" = "$HB" ] || fail "manifest mismatch $HA != $HB"
pass "S3 deterministic ($HA)"

echo "--- S4: PR exclusivity ---"
# Use the newest commit time as the fake PR start: snapshot must exclude it.
PR_START=$(git -C "$REPO_ROOT" log -1 --format=%cI)
TOTAL=$(git -C "$REPO_ROOT" rev-list --count HEAD)
rm -rf /tmp/tb-snap-s4 && go run ./cmd/snapshot --repo "$REPO_ROOT" \
  --cutoff-type pr --pr 999 --pr-start-override "$PR_START" \
  --out /tmp/tb-snap-s4 > /dev/null
python3 - "/tmp/tb-snap-s4/history.json" "$TOTAL" <<'EOF'
import json, sys
payload = json.load(open(sys.argv[1]))
total = int(sys.argv[2])
assert len(payload["commits"]) == total - 1, \
  f"expected {total-1} commits, got {len(payload['commits'])}"
print(f"pr-exclusive commits={len(payload['commits'])}/{total}")
EOF
pass "S4 PR start $PR_START excluded"

echo "ALL CHECKS PASSED"
