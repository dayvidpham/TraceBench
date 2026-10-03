# OpenCode copy trial

The copy starts. `opencode --version` inside the container prints `1.18.34`.

## Pin

`harness/opencode/version` is `v1.18.34`. That tag is the GitHub latest release, and the release contains `opencode-linux-x64.tar.gz`. Tag `v2.0.22` has no GitHub release, so it has no linux release binary. OpenCode was not built inside the image.

`pull.sh` fetched that tag with depth 1. `rev-list --count --all` was 1. `REVISION` is `aec0b9a6d8898f68f923aaf08b7306d931fd9d76`. `src/.git` was removed. `find harness/opencode/src -name .git` prints nothing.

The linux binary is the single `opencode` file from `opencode-linux-x64.tar.gz`, at `harness/opencode/bin/opencode`.

| File | sha256 |
| --- | --- |
| `harness/opencode/REVISION` | `9452a87f49dcecb98cb10bb74e4c4dd6c838145bea9cd6170873f1bd43696068` |
| `harness/opencode/bin/opencode` | `9ca0b9953d49997601655e54f846a3efa464f237e47c6f1b04716d0f2e64c4c2` |

## One run

Podman 5.4.2 was installed for this trial (it was not on `PATH`). The run was rootless. `tasks/trace-propagation/environment/Dockerfile` and `task.toml` were not edited. The image is `tracebench-trace-propagation`, built from that Dockerfile. No network flag was passed. No volume or bind mount was used.

`tracebench-trace-propagation` keeps the base command `["python3"]`. A container created without `-i` (`tb-opencode-plain`) inspected as `created []`, and `podman start` left it `exited` with code 0. `podman exec` cannot attach to that state.

The checked container is `tb-opencode-trial`, created with `-i` so the same `python3` command stays up while a fifo holds stdin. Files were copied while it was stopped:

- `harness/opencode/src` → `/opt/opencode`
- `harness/opencode/bin/opencode` → `/usr/local/bin/opencode`
- `harness/opencode/REVISION` → `/opt/opencode/REVISION`

After create, inspect was `created []`. After start, `Mounts` was still `[]`.

## Checks

All of these passed in `tb-opencode-trial`:

- `sha256sum` of `/opt/opencode/REVISION` and `/usr/local/bin/opencode` matched the host payload above.
- `stat -c '%u'` on both paths was `0`.
- `find /opt/opencode -name .git` printed nothing.
- `opencode --version` stdout was `1.18.34`, the same ref as `v1.18.34`. Stderr was empty.

`prove.sh` is that run. It stops the container on the way out (`tb-opencode-trial` then exits from that stop).

## Reuse

The live container was not committed. This build succeeded:

```bash
podman build --build-arg BASE=tracebench-trace-propagation \
  -f harness/opencode/Containerfile \
  -t tracebench-opencode-reuse \
  harness/opencode
```

`podman run --rm localhost/tracebench-opencode-reuse opencode --version` printed `1.18.34`. The same two checksums matched, both paths were uid 0, and `find /opt/opencode -name .git` printed nothing.
