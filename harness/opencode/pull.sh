#!/usr/bin/env bash
# Download the OpenCode release binary configured in harness/config.json.
# The payload is only harness/opencode/bin/opencode; no source checkout exists.
set -euo pipefail
cd "$(dirname "$0")/../.."

ref=$(go run harness/opencode/configure.go version harness/config.json)

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
