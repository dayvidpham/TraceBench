# tasks/_base — shared build cache for PR-derived tasks (issue #27)

`peasant-base.Dockerfile` builds `tracebench/peasant-base:<SNAPSHOT_SHA>`:
full peasant history + warmed Go module/build caches. Task images build
`FROM` it, check out their (older) base commit, truncate, and rebuild.

## Build order

```bash
# 1. Base (one-time, ~5-8 min: full clone + go mod download + go build):
podman build -f tasks/_base/peasant-base.Dockerfile \
  -t tracebench/peasant-base:838a6dd0a73524db6ae96a931c224ff66090aa70 .

# 2. Task (Harbor builds this itself; incremental rebuild only):
harbor run -p tasks/peasant-344 -a oracle -e podman
```

Task Dockerfiles reference the base by pinned tag. Bump `SNAPSHOT_SHA`
deliberately: it must postdate every task's base commit.

## Leak model (what we truncate and why)

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
| Go module cache | safe: dependency sources only, no peasant code |
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
