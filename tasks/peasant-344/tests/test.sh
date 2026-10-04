#!/bin/bash
# Verifier for peasant-344. Runs OUTSIDE the agent container in Harbor's
# locked-down verifier phase (no-network) and reports only pass/fail.
# The image sets GOPROXY=off, so this also proves the run needs no network.
set -u

cd /peasant

# Guard 1: the new test file must exist (it does not at base).
if [ ! -f internal/api/sync_publication_test.go ]; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: sync_publication_test.go missing (base state, not fixed)"
  exit 0
fi

# Guard 2: the fixture must exist.
if [ ! -f internal/api/testdata/sync_publication_validation.yaml ]; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: sync_publication_validation.yaml missing"
  exit 0
fi

# Fail-to-pass tests from the PR (3 regression cases + Share-door case).
OUT=$(go test -count=1 -v ./internal/api/ \
  -run 'TestHandleSyncRedactionsRefusesUnpublishableCapture|TestHandleSyncPush_TheShareDoorGivesThePipelineARedactor' 2>&1)
STATUS=$?

echo "$OUT" | tail -20

# Guard 3: "no tests to run" counts as FAIL (base state without the fix).
if echo "$OUT" | grep -q "no tests to run"; then
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL: no tests matched (fix not applied)"
  exit 0
fi

if [ $STATUS -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
  echo "PASS"
else
  echo 0 > /logs/verifier/reward.txt
  echo "FAIL"
fi
