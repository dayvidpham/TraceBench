# tasks/_base — shared build cache for PR-derived tasks (issue #27)

`peasant-base.Dockerfile` builds `tracebench/peasant-base:<SNAPSHOT_SHA>`:
full peasant history + warmed Go module/build caches. Task images build
`FROM` it, check out their (older) base commit, truncate, and rebuild.

## Build order

```bash
# 1. Base (one-time, ~5-8 min: full clone + go mod download + go build):
podman build -f tasks/_base/peasant-base.Dockerfile \
  -t tracebench/peasant-base:838a6dd0a73524db6ae96a931c224ff66090aa70 \
  -t tracebench/peasant-base:latest \
  -t tracebench/peasant-base:keep .

# 2. Runtime (seconds; strips the clone and build cache, adds /workdir):
podman build -f tasks/_base/task-runtime.Dockerfile \
  -t tracebench/task-runtime:latest \
  -t tracebench/task-runtime:keep .

# 3. Task (Harbor builds this itself; incremental rebuild only):
harbor run -p tasks/peasant-344 -a oracle -e podman
```

Task Dockerfiles reference the base by pinned tag. Bump `SNAPSHOT_SHA`
deliberately: it must postdate every task's base commit.

### Surviving Harbor teardown

Harbor's compose teardown runs `down --rmi local`, which removes the image
referenced by the task — `tracebench/task-runtime:latest` can disappear after
any run. The `:keep` aliases above hold the images across teardown; run
`scripts/ensure-task-images.sh` before each Harbor run to re-tag `:latest`
from them (it fails closed when the alias is gone and a rebuild is needed).

## Runtime image for generated tasks

`task-runtime.Dockerfile` builds `tracebench/task-runtime:latest`, the default
`docker_image` of every task the loader pipeline generates. Generated tasks
build no per-task image: Harbor uploads `environment/repo/` (a truncated
repo holding the full real history truncated at the base SHA) into `/workdir` at start.

| Base-image content | Runtime image |
|---|---|
| `/peasant` (full live clone; later fix commits reachable) | removed |
| `/root/.cache/go-build` (post-fix symbols in export data) | emptied with `go clean -cache` |
| `/go/pkg/mod` (dependency sources only) | kept: no peasant code |
| `/workdir` (Harbor's container start `chdir`s into it) | created; `WORKDIR` |
| network | `GOPROXY=off`, `GOFLAGS=-mod=readonly` |

Verify after a build:

```bash
podman run --rm -w / tracebench/task-runtime:latest sh -c \
  'ls -d /workdir; ls -d /peasant; find /root/.cache/go-build -type f; du -sh /go/pkg/mod'
```

Expect `/workdir`, no `/peasant`, only Go's `README`/`trim.txt` markers in the
build cache, and a populated module cache.

### Dependency warm at environment start

The runtime image's module cache is warmed at the snapshot commit, and a task's
base commit can pin older dependency versions than the snapshot (`redact
v0.1.5` vs `v0.1.6+`, for example). Generated tasks therefore carry an
`[environment.healthcheck]` command that runs `go mod download` and the build
command once, while the environment network is still up (the healthcheck runs
after the task data is uploaded and before the agent phase). The agent and
verifier phases keep `GOPROXY=off` and no-network, resolving everything from
the warmed cache. The healthcheck doubles as a readiness check: the pre-PR tree
must build before the agent starts.

## Leak model of hand-built tasks (what we truncate and why)

The agent must find exactly the base tree — nothing that names or contains
the fix. Vectors checked, per task build (asserts in the task Dockerfile
fail the build if any trip):

| Vector | Mitigation |
|---|---|
| branches, tags, `packed-refs` | delete every ref (`for-each-ref` must be empty) |
| reflogs, `HEAD@{n}`, `.git/logs` | `reflog expire --all`, remove logs dir |
| `FETCH_HEAD`, `ORIG_HEAD`, stash | removed / never created |
| dangling objects (`fsck --unreachable`) | `gc --prune=now` after ref deletion |
| remotes (re-fetch the fix) | `git remote remove origin` (agent phase is offline anyway) |
| Go build cache (export data names future functions) | `go clean -cache` + full rebuild at task build time |
| Go module cache | safe: dependency sources only, no peasant code — but the base commit may pin *different dep versions* than the snapshot warmed; generated tasks warm them at environment start (healthcheck, network public) and the agent/verifier phases stay offline |
| `/solution`, `/tests` in container | Harbor copies them for oracle/verifier runs only — absent during agent phase |
| Dockerfile `ARG`s / image history | base SHA is public task metadata; the patch never enters any image layer (mounted at oracle time) |

Accepted residual risks (documented, not fixed here):

* **Local image layers.** Task images built `FROM` the base keep the base's
  layers (with full `.git`) in the *local* image store. The running
  container only sees the truncated filesystem, which is the trust boundary
  for the agent — but **squash the image before any registry publish**
  (`podman build --squash`), or the fix ships in lower layers.
* **Model channel.** The agent can exfiltrate the task through the allowed
  model host (`--allow-agent-host`). That is inherent to agent benchmarks;
  only the oracle is secret, never the task.
* **Host job artifacts** (`jobs/`) never enter the container.
