#!/bin/bash
# Oracle for peasant-344: check out the PR files from the baked pr-344 ref.
# Works offline — the ref is fetched at image build time, not here.
# NOTE: the PR *renames* sync_door_redaction_test.go -> sync_publication_test.go
# (R078), so the old file must be removed or the package won't compile
# (duplicate TestHandleSyncPush..., syncDoorSecret, helpers).
set -euo pipefail

cd /peasant

git checkout pr-344 -- \
  cmd/peasant/cmd_push.go \
  internal/api/sync_handler.go \
  internal/api/sync_publication_test.go \
  internal/api/testdata/sync_publication_validation.yaml \
  internal/push/pipeline.go \
  internal/push/publication_input.go

rm -f internal/api/sync_door_redaction_test.go

go build ./...
echo "oracle applied: peasant#344 head files checked out"
