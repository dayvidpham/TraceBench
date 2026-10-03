#!/usr/bin/env bash
# Shallow-fetch the ref in harness/opencode/version. One commit, then no .git.
# Also fetch the linux release binary for this machine's arch.
# Payload lands in harness/opencode/{src,REVISION,bin/opencode}.
set -euo pipefail
cd "$(dirname "$0")/../.."

ref=$(tr -d '[:space:]' < harness/opencode/version)
git init harness/opencode/src
git -C harness/opencode/src remote add origin https://github.com/anomalyco/opencode.git
git -C harness/opencode/src fetch --depth 1 origin "$ref"
git -C harness/opencode/src checkout --detach FETCH_HEAD
test "$(git -C harness/opencode/src rev-list --count --all)" = 1
git -C harness/opencode/src rev-parse HEAD > harness/opencode/REVISION
rm -rf harness/opencode/src/.git

case "$(uname -m)" in
  arm64|aarch64) asset=opencode-linux-arm64.tar.gz ;;
  x86_64|amd64) asset=opencode-linux-x64.tar.gz ;;
  *)
    echo "no OpenCode linux asset for $(uname -m)" >&2
    exit 1
    ;;
esac

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
curl -fsSL -o "$stage/opencode.tgz" \
  "https://github.com/anomalyco/opencode/releases/download/${ref}/${asset}"
tar -xzf "$stage/opencode.tgz" -C "$stage"
bin=$(find "$stage" -type f -name opencode ! -path '*/.*' -print -quit)
test -n "$bin"
mkdir -p harness/opencode/bin
cp "$bin" harness/opencode/bin/opencode
chmod +x harness/opencode/bin/opencode
