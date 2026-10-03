#!/usr/bin/env bash
# Shallow-fetch the ref in harness/opencode/version. One commit, then no .git.
# Run from anywhere; payload lands in harness/opencode/{src,REVISION}.
set -euo pipefail
cd "$(dirname "$0")/../.."

ref=$(cat harness/opencode/version)
git init harness/opencode/src
git -C harness/opencode/src remote add origin https://github.com/anomalyco/opencode.git
git -C harness/opencode/src fetch --depth 1 origin "$ref"
git -C harness/opencode/src checkout --detach FETCH_HEAD
test "$(git -C harness/opencode/src rev-list --count --all)" = 1
git -C harness/opencode/src rev-parse HEAD > harness/opencode/REVISION
rm -rf harness/opencode/src/.git
