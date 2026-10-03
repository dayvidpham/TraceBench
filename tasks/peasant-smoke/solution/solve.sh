#!/bin/bash
# Oracle for a smoke task: the pinned codebase already builds and passes, so
# there is nothing to fix. The oracle proves the containerized task runs
# end-to-end (image -> agent phase -> verifier -> reward 1.0).
set -euo pipefail

cd /peasant
git log --oneline -1
go version
echo "smoke oracle: nothing to change"
