FROM golang:1.25-bookworm

# Toolchain + agent-runtime deps in one layer. nodejs/npm (+curl) are baked
# because Harbor installed-agents (e.g. opencode) probe for
# `curl bash stdbuf node npm`, and that probe's package-manager fallback
# crashes on Podman Desktop for Mac (compose-shim warning pollutes stdout).
# (bash/coreutils ship with bookworm; node here is only a placeholder —
# Harbor installs a modern node via nvm at agent-setup time.)
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

ARG PEASANT_REPO=https://github.com/peasant-labs/peasant.git
# Snapshot commit the caches are warmed at. Must be NEWER than every task's
# base commit (task stages check out older commits from this history).
# Bump deliberately and retag; see README.md.
ARG SNAPSHOT_SHA=838a6dd0a73524db6ae96a931c224ff66090aa70

# Full history on purpose: task stages derive their base trees from this.
RUN git clone "${PEASANT_REPO}" /peasant \
    && cd /peasant \
    && git checkout "${SNAPSHOT_SHA}" \
    && git log --oneline -1

WORKDIR /peasant

# Warm the module and build caches (the ~5 min cost, paid once here).
# WARNING: GOCACHE now contains post-fix symbols (export data names future
# functions). Task stages MUST `go clean -cache` and rebuild after checking
# out their older base — see tasks/peasant-344/environment/Dockerfile.
RUN go mod download && go build ./...

ENV GOPROXY=off GOFLAGS=-mod=readonly
