#!/bin/bash
# Verifier for peasant-smoke: full build plus the fast smoke-test package.
# The image sets GOPROXY=off, so this also proves the run needs no network.
set -u

cd /peasant
go build ./... && go test -count=1 ./internal/defaults/...

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
