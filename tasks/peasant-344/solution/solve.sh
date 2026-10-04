#!/bin/bash
# Oracle for peasant-344: apply the PR diff baked into solution/oracle.patch.
# This file (and the whole solution/ dir) is copied into the container by
# Harbor's OracleAgent only — it is never visible during agent runs (issue #27).
# The patch includes the rename sync_door_redaction_test.go ->
# sync_publication_test.go, so git apply removes the old file; without that
# the package fails to compile on duplicate declarations.
set -euo pipefail

cd /peasant

git apply --check /solution/oracle.patch
git apply --index /solution/oracle.patch

go build ./...
echo "oracle applied: peasant#344 patch applied from /solution/oracle.patch"
