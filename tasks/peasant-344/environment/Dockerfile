FROM golang:1.25-bookworm

# git for the checkout; ca-certificates for module downloads at build time.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG PEASANT_REPO=https://github.com/peasant-labs/peasant.git
# Base commit of peasant#344 (pre-PR, post-#337 on develop). Task starts here.
ARG BASE_SHA=d3bc07f6568291ee70afb146409aae98d4f8e3d7
# PR head ref, fetched at build time so the oracle works offline.
ARG PR_REF=pull/344/head

# Full clone pinned to base. History is kept intentionally: the oracle
# checks out the 6 PR files from the fetched head without network.
RUN git clone "${PEASANT_REPO}" /peasant \
    && cd /peasant \
    && git checkout "${BASE_SHA}" \
    && git fetch origin "${PR_REF}:pr-344" \
    && git log --oneline -1 && git log --oneline pr-344 -1

WORKDIR /peasant

# Bake the module cache and build artifacts into the image so that agent and
# verifier phases need zero network access.
RUN go mod download && go build ./...

# Hermetic by construction: no module fetches after image build.
ENV GOPROXY=off GOFLAGS=-mod=readonly

# Sanity check: base builds. The new publication tests do NOT exist at base
# (expected: [no tests to run] — the agent's job is to make them pass).
RUN go build ./...
