FROM golang:1.25-bookworm

# git for the checkout; ca-certificates for module downloads at build time.
# nodejs/npm (+curl) are pre-installed so Harbor installed-agents (e.g. opencode)
# pass their `command -v curl bash stdbuf node npm` check and skip the
# package-manager probe — that probe breaks on Podman Desktop for Mac, where
# the compose-shim warning pollutes stdout and crashes agent setup with
# KeyError. (bash/coreutils ship with bookworm; node here is only a placeholder,
# Harbor installs a modern node via nvm at agent-setup time.)
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

ARG PEASANT_REPO=https://github.com/peasant-labs/peasant.git
# Base commit of peasant#344 (pre-PR, post-#337 on develop). Task starts here.
ARG BASE_SHA=d3bc07f6568291ee70afb146409aae98d4f8e3d7

# Base tree ONLY (issue #27): init an empty repo and fetch just the base
# commit by SHA, then drop the remote. The image contains exactly one commit,
# zero refs, and no oracle material — the answer key is not discoverable via
# `git log --all`, tags, branches, or fetches (a plain `--depth 1` clone
# would still pull the default-branch tip, which postdates the fix).
# The oracle patch ships in solution/oracle.patch, which Harbor copies into
# the container for oracle runs only (never visible to the agent).
RUN git init /peasant \
    && cd /peasant \
    && git remote add origin "${PEASANT_REPO}" \
    && git fetch --depth 1 origin "${BASE_SHA}" \
    && git checkout "${BASE_SHA}" \
    && git remote remove origin \
    && git log --oneline -1 && git for-each-ref

WORKDIR /peasant

# Bake the module cache and build artifacts into the image so that agent and
# verifier phases need zero network access.
RUN go mod download && go build ./...

# Hermetic by construction: no module fetches after image build.
ENV GOPROXY=off GOFLAGS=-mod=readonly

# Sanity check: base builds. The new publication tests do NOT exist at base
# (expected: [no tests to run] — the agent's job is to make them pass).
RUN go build ./...
