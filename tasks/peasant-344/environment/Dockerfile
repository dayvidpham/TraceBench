FROM tracebench/peasant-base:838a6dd0a73524db6ae96a931c224ff66090aa70
# ^ built via tasks/_base (one-time cache warmer). If the base image is
# absent, build it first — see tasks/_base/README.md.

# Base commit of peasant#344 (pre-PR, post-#337 on develop). Task starts here.
# (Snapshot commit of the base image must postdate this.)
ARG BASE_SHA=d3bc07f6568291ee70afb146409aae98d4f8e3d7

# Base tree ONLY (issue #27): check out the base commit, then truncate every
# history vector — refs, reflogs, remotes, unreachable objects — so the fix
# (which exists upstream of this checkout) is undiscoverable. The oracle
# patch ships in solution/oracle.patch, copied in for oracle runs only.
# NOTE: `git remote remove origin` runs FIRST because batch-deleting refs
# trips over the origin/HEAD symref ("multiple updates ... via symref").
RUN cd /peasant \
    && git checkout --quiet "${BASE_SHA}" \
    && git remote remove origin \
    && git for-each-ref --format='delete %(refname)' | git update-ref --stdin \
    && git reflog expire --expire=now --all \
    && git gc --prune=now --quiet \
    && rm -rf .git/logs .git/FETCH_HEAD .git/ORIG_HEAD \
    && echo "--- leak asserts (build-breakers) ---" \
    && test "$(git rev-list --all --count)" = 1 \
    && test -z "$(git for-each-ref)" \
    && test -z "$(git fsck --unreachable 2>/dev/null)" \
    && git rev-parse pr-344 >/dev/null 2>&1 && exit 1 || true \
    && git log --oneline -1

WORKDIR /peasant

# Rebuild at the base commit. Two subtleties:
# - `go clean -cache` first: the base image's cache contains post-fix export
#   data (future function names) — it must not ship. Module cache is kept
#   (dependency sources only, no peasant code).
# - GOPROXY is reopened here: the base commit's go.mod can pin DIFFERENT dep
#   versions than the base image warmed, and image build phase has network.
#   It is locked back down right after; the final `go build` proves offline.
ENV GOPROXY=https://proxy.golang.org,direct
RUN go clean -cache && go mod download && go build ./...

# Hermetic by construction: no module fetches after image build.
ENV GOPROXY=off GOFLAGS=-mod=readonly

# Sanity check: base builds. The new publication tests do NOT exist at base
# (expected: [no tests to run] — the agent's job is to make them pass).
RUN go build ./...
