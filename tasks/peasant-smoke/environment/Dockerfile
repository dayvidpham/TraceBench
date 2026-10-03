FROM golang:1.25-bookworm

# git for the checkout; ca-certificates for module downloads at build time.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG PEASANT_REPO=https://github.com/peasant-labs/peasant.git
# Pinned commit of peasant-labs/peasant (develop, 2026-10-03). Bump deliberately.
ARG PEASANT_SHA=4153b0026c9e73157ef663368a7bb497c022afc1

# Full clone, pinned to SHA. History is kept intentionally: later snapshot /
# cutoff work (#2, #19) needs commits before the task cutoff, not just a tree.
RUN git clone "${PEASANT_REPO}" /peasant \
    && cd /peasant && git checkout "${PEASANT_SHA}" && git log --oneline -1

WORKDIR /peasant

# Bake the module cache and build artifacts into the image so that agent and
# verifier phases need zero network access.
RUN go mod download && go build ./...

# Hermetic by construction: no module fetches after image build.
ENV GOPROXY=off GOFLAGS=-mod=readonly

# Sanity check: the smoke-test package every run exercises must pass baked in.
RUN go test -count=1 ./internal/defaults/...
