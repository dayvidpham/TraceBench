# Proof of concept: run an eval with the generated pipeline

The loader turns a list of merged pull requests into runnable Harbor tasks and one Harbor job
config; Harbor runs the job. Expected: **0 exceptions, ~0.998** (the residual is a documented
calibration set). Validated on 2026-10-04 (see the PR #51 review report).

## Dependencies

| Dependency | Why | Get it |
|---|---|---|
| podman ≥ 5 | container runtime (Harbor's `podman` environment) | system package |
| Harbor ≥ 0.23 | task runner (`harbor run`) | `uv tool install harbor` |
| docker-compose v2 | Harbor's podman provider shells out to `docker compose` | your package manager; Nix users get it from the devShell |
| git | the loader resolves boundaries and packs the secure worktree | your package manager |
| Go ≥ 1.25 | `tracebench-sample` index/dump and the `snapshot` module (not needed to run tasks) | your package manager |
| a `peasant-labs/peasant` clone | source of `tree_commit` / `merge_commit` | `git clone` |
| a corpus dump + index | prior traces and PR metadata | `go run ./cmd/tracebench-sample index` + `dump` |

## Images (build once)

```bash
# 1. Base (~5–8 min): full clone + warmed Go module/build caches.
podman build -f tasks/_base/peasant-base.Dockerfile \
  -t tracebench/peasant-base:838a6dd0a73524db6ae96a931c224ff66090aa70 \
  -t tracebench/peasant-base:latest \
  -t tracebench/peasant-base:keep .

# 2. Runtime (seconds): strips the clone and build cache, adds /workdir.
podman build -f tasks/_base/task-runtime.Dockerfile \
  -t tracebench/task-runtime:latest \
  -t tracebench/task-runtime:keep .
```

**Before every Harbor run**, re-tag from the `:keep` aliases:

```bash
scripts/ensure-task-images.sh
```

Harbor's compose teardown runs `down --rmi local`, which removes the image a task references, so
`tracebench/task-runtime:latest` can disappear after any run. The `:keep` tags hold the images;
the script re-tags `:latest` from them and fails closed when a rebuild is needed. If `:keep` is
gone, rebuild per `tasks/_base/README.md`.

## Run the generated pipeline

Generate a task set from a PR list:

```bash
uv sync
printf 'peasant-labs/peasant#344\npeasant-labs/peasant#406\npeasant-labs/peasant#527\n' > /tmp/prs.txt
uv run tracebench-corpus --corpus corpus/dump pipeline \
  --prs /tmp/prs.txt \
  --repo-dir /path/to/peasant-clone \
  --index corpus/index/merged_prs.json \
  --dest build/mvp \
  --run-id tracebench-mvp
```

Run it (the generated job config carries `n_attempts: 3` for real evals; `-k 1` keeps a smoke
fast):

```bash
scripts/ensure-task-images.sh
harbor run -c build/mvp/job-config-tracebench-mvp.yaml -e podman -k 1
```

Expect 3/3 trials, 0 exceptions, rewards around 0.998, and **0 missing cases**. Each task:

- uploads `environment/repo/` — the project's full real history truncated at the pre-PR commit,
  on the PR's base branch (`develop`), with the fix commit absent and no remotes;
- runs an environment healthcheck that marks the repo safe for git and warms `go mod download`
  plus the build while the network is up;
- then runs the agent and verifier offline (`GOPROXY=off`, no network).

The residual (one root-only failure on #344/#406 plus 6–7 environment-conditional skips) is the
calibration set; baseline calibration is the follow-up that lifts the ceiling to 1.0.

## Local installed OpenCode credentials

Use `tracebench-harbor` for local OpenCode jobs. It uses the installed Harbor OpenCode agent,
not a custom harness. The adapter supports Harbor **0.23.0** and Python **3.12 or newer**.
The optional dependency pins Harbor because its private exec boundary is version-specific;
the development dependencies also install it for production-path contract tests.

Prepare a public Harbor job config with `environment.type: podman`, a fresh `job_name`, and
the **existing selected** `agents[].model_name`. Keep the model's `opencode-go/` prefix for
OpenCode Go; `opencode/` means Zen. The launcher does not select or change the model. Generated
job configs, target configurations, payloads and tasks remain credential-free. Task generation
does not read any credential.

Harbor installs OpenCode `latest` unless `agents[].kwargs.version` is set. To use OpenCode
1.18.34, set that field to `"1.18.34"` in the public job config; the Harbor version pin is separate.

```bash
uv sync
# OPENCODE_API_KEY must already be set in the launching process. Do not paste a key here.
uv run tracebench-harbor -c build/local-opencode-job.yaml
# Or select another already-set host variable by NAME, not by value:
uv run tracebench-harbor -c build/local-opencode-job.yaml --opencode-key-env BENCH_GO_KEY
```

The launcher rejects an invalid variable name or an absent/empty value before creating a job.
It does not validate key contents or call an auth endpoint. It puts only
`OPENCODE_API_KEY: ${BENCH_GO_KEY}` (or `${OPENCODE_API_KEY}`) in the in-memory agent config;
Harbor persists that named reference. The installed env resolver and trial scope forward it at
runtime. At the exec boundary, the adapter resolves the Compose service's container ID and uses
`podman exec -e OPENCODE_API_KEY ...` with a private per-subprocess environment mapping. It avoids
Compose frontends that could re-expand a named reference into a literal engine argument. It
does not change the host environment, even when trials run in parallel.

The adapter guards raw and JSON-escaped credentials in normal OpenCode output **before** Harbor's
tee writes it, sanitizes host logs/errors and exec output, and scrubs downloaded job artifacts
on success, error or cancellation.
Binary outputs with a match are scrubbed too and may no longer be usable. Exec progress output
is delayed until that command completes so values that span lines do not leak. If artifact
guarding fails, do not publish the output directory. The wrapper supports new local jobs, not
resuming an existing output directory. Oracle and other agents bypass all credential checks and
adapter handling; use ordinary Harbor for other launch modes.

**Accepted exposure:** shell tools and container administrators can read the runtime environment.
This is not confidentiality from a malicious agent. Do not inspect full container environments
or run environment-dump commands. The launcher never reads, copies or mounts host auth databases.
It does not transfer OAuth or make credential probes. No API key belongs in an instruction,
prompt, CLI argument, image, build context, configuration or published evidence.

Task network and tool policies remain unchanged. Generated tasks have `no-network` during the
agent phase. An installed agent's provider request may therefore fail even when passthrough
succeeds. Record that as a network/runtime failure, not as a missing variable; do not relax task
policy. Use only the approved short actual OpenCode run for live evidence, with its existing
model selection. The fixture tests use fake credentials and make no provider requests:

```bash
uv run pytest loaders/tests/test_auth_loading.py
```

### Credential test promotion record

- Invariant: the selected environment key reaches the server process but not exec argv, routine
  output, recorded config, logs or downloaded output, on success and failure.
- Why a pure unit test is insufficient: Harbor resolves templates and merges scoped env after
  agent construction; its Podman exec builder used to serialize values into Compose argv.
- Production path: launcher selection, installed `AgentFactory`, `OpenCode.run`, trial-style
  `scoped_exec_env`, Podman exec/Compose builder, adapter, and installed output collectors.
- Transport fixture: a real local child process stands in for the container transport. The fake
  OpenCode binary checks receipt without printing a value as evidence; it deliberately emits fake
  markers to test the guards. This is not a live OpenCode integration result.
- Bound/lifetime: each stream-guard child has a ten-second timeout; pytest owns temporary files
  and fake executables. Contract exec tests use the installed collector with a ten-second limit.
  No container, database or provider call is created by the default test suite.
  The blocked-resolution child sleeps at most thirty seconds; a 0.1-second exec deadline must
  kill and reap it, and the test has a five-second outer bound.
- Isolation: each test has a separate temp directory and fake environment; Harbor's per-task env
  scopes and per-subprocess mappings prevent host override mutation. Adapter ownership excludes
nested launchers; all patches restore on exit.
- Exec timeout: container resolution and engine exec share one deadline. Owned subprocesses
  are killed and reaped on timeout or cancellation, including a blocked Compose provider.
- Clean checkout: `uv run pytest` installs the pinned runtime through dev dependencies on Python
  3.12+. On older Python the installed-Harbor test module is skipped; use 3.12+ for this gate.
- Evidence/mutation: removing the named-only mapping exposes a fake marker in argv; removing the
  stream guard exposes a fake marker in `opencode.txt` before artifact cleanup; removing output
  or binary artifact cleanup fails nondisclosure assertions.
- Simplification: remove the compatibility adapter once installed Harbor has an equivalent
  named-only local transport and output contract; keep the boundary tests against that path.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `denied: requested access … tracebench/task-runtime` | Harbor's teardown removed the image the task references | `scripts/ensure-task-images.sh`; rebuild if `:keep` is gone |
| `crun: chdir to '/workdir'` | stale base image without `/workdir` | rebuild the runtime image |
| `HealthcheckError` | the warm failed (network or command error) | read the trial log and `/logs/agent/healthcheck.log`; the environment phase must reach the Go proxy |
| `module lookup disabled by GOPROXY=off` | the warm did not run | regenerate the task (the healthcheck ships in `task.toml`) |
| TOML parse error (`split = null`, `pr_url = null`) | task generated by an old loader | regenerate with `--force` |
| Oracle below 1.0 | known residual | compare against the calibration list in the PR #51 report |
