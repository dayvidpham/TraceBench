# Peasant smoke task (containerization check, issue #1)

The Peasant codebase (`peasant-labs/peasant`, pinned commit — see
`environment/Dockerfile` `PEASANT_SHA`) lives at `/peasant` **inside the
container image**. It is the only codebase you may use. There is no host
checkout to fall back on.

## Your task

1. Build the binary:
   ```bash
   go build ./...
   ```
2. Run the smoke-test package:
   ```bash
   go test -count=1 ./internal/defaults/...
   ```

Both must succeed. The module cache is baked into the image and the
environment is hermetic (`GOPROXY=off`), so these commands need no network.

## Constraints

* Work only in `/peasant`. Do not expect any host files to exist.
* Do not change the pinned commit or fetch new code — later benchmark tasks
  are built from specific PRs; this task pins one commit on purpose.
