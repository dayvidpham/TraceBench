# Runtime base for generated tasks: the shared toolchain image with every
# post-fix artifact removed. The peasant clone (/peasant) reaches later commits
# and the Go build cache holds post-fix symbols; both go. The module cache
# (/go/pkg/mod, dependency sources only) stays. Harbor starts containers in
# /workdir, so it must exist. The base WORKDIR is /peasant, so leave it first.
FROM tracebench/peasant-base:latest
WORKDIR /
RUN rm -rf /peasant && go clean -cache && mkdir -p /workdir
# VCS stamping is off: uploaded repositories are foreign to the container user,
# so git-based stamping fails builds that the task does not need stamped.
ENV GOPROXY=off GOFLAGS="-mod=readonly -buildvcs=false"
WORKDIR /workdir
