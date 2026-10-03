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
# Cross-check against git itself: snapshot must contain exactly the commits
# git sees at/before the cutoff (committer date, same clock as ListHistory).
WANT=$(git -C "$REPO_ROOT" rev-list --count --before="$CUTOFF" HEAD)
GOT=$(jq '.commits | length' /tmp/tb-snap-s2/history.json)
[ "$GOT" -gt 0 ] || fail "expected non-empty history"
[ "$GOT" = "$WANT" ] || fail "commits $GOT != git rev-list --before count $WANT"
FILES=$(jq '.files | length' /tmp/tb-snap-s2/history.json)
echo "commits=$GOT files=$FILES"
pass "S2 $GOT commits match git at $CUTOFF"

echo "--- S3: determinism ---"
rm -rf /tmp/tb-snap-s3a /tmp/tb-snap-s3b
go run ./cmd/snapshot --repo "$REPO_ROOT" --cutoff-type date \
  --cutoff-date "$CUTOFF" --out /tmp/tb-snap-s3a > /dev/null
go run ./cmd/snapshot --repo "$REPO_ROOT" --cutoff-type date \
  --cutoff-date "$CUTOFF" --out /tmp/tb-snap-s3b > /dev/null
HA=$(jq -r '.manifest_sha256' /tmp/tb-snap-s3a/history.json)
HB=$(jq -r '.manifest_sha256' /tmp/tb-snap-s3b/history.json)
[ "$HA" = "$HB" ] || fail "manifest mismatch $HA != $HB"
pass "S3 deterministic ($HA)"

echo "--- S4: PR exclusivity ---"
# Use the newest commit time as the fake PR start: snapshot must exclude it.
PR_START=$(git -C "$REPO_ROOT" log -1 --format=%cI)
TOTAL=$(git -C "$REPO_ROOT" rev-list --count HEAD)
rm -rf /tmp/tb-snap-s4 && go run ./cmd/snapshot --repo "$REPO_ROOT" \
  --cutoff-type pr --pr 999 --pr-start-override "$PR_START" \
  --out /tmp/tb-snap-s4 > /dev/null
GOT_PR=$(jq '.commits | length' /tmp/tb-snap-s4/history.json)
[ "$GOT_PR" = "$((TOTAL - 1))" ] || fail "expected $((TOTAL-1)) commits, got $GOT_PR"
echo "pr-exclusive commits=$GOT_PR/$TOTAL"
pass "S4 PR start $PR_START excluded"

echo "ALL CHECKS PASSED"
