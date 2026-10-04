# TraceBench

Agent benchmarks with **network-restricted sandboxes**. Tasks are built on
[Harbor](https://harborframework.com) (`hub.harborframework.com` hosts shared
datasets/tasks/leaderboards; the sandbox that actually enforces isolation is the
provider you run with, e.g. local `docker`).

Goal: agents that can do real engineering work **without** hacking their way out
— no curl-ing solutions, no pip-installing bypasses, no exfiltrating tests.

## How isolation works

Each task declares a network policy in `task.toml`:

```toml
[environment]  # baseline at start — public so agent.setup() can install
network_mode = "public"

[agent]        # during agent.run() — locked down
network_mode = "no-network"

[verifier]     # during verify() — locked down
network_mode = "no-network"
```

* `public` / `no-network` / `allowlist` (with `allowed_hosts`) per phase.
* Deps are baked into the image (`environment/Dockerfile`) so run/verify
  phases need zero egress.
* Tests use randomized ids so hardcoded golden files can't pass.

See `tasks/trace-propagation/task.toml` for the reference example.

## Repo layout

```
tasks/<name>/
  instruction.md          # what the agent must do
  task.toml               # metadata + network policy + timeouts
  environment/
    Dockerfile            # sandbox image (deps pre-installed)
    app/                  # repo snapshot the agent works on
  solution/solve.sh       # oracle fix (proves the task is solvable)
  tests/
    test.sh               # offline verifier, writes /logs/verifier/reward.txt
    test_outputs.py       # pytest grading (randomized, behavioral)
```

## Quickstart

```bash
uv tool install harbor

# Prove the task is solvable (oracle runs solution, then verifier):
harbor run -p tasks/trace-propagation -a oracle -e docker

# Smoke-test a real agent (needs network ONLY on the host for the model API;
# the sandbox itself stays offline during the run):
harbor run -p tasks/trace-propagation -a claude-code \
  -m anthropic/claude-haiku-4-5 -e docker

# Inspect trajectories / verifier logs:
harbor view ./jobs
```

## Tasks

| Task | What it tests | Difficulty |
| - | - | - |
| `tracebench/trace-propagation` | Fix W3C `traceparent` propagation across two services (trace-id, sampled flag, tracestate) | Medium: 3 files to read, reproduce, fix 1 function |
| `tracebench/peasant-smoke` | Containerized Peasant codebase builds + fast unit tests pass (issue #1) | Smoke: no bug fix, proves image-origin and host cleanliness |

## Corpus sampling

`cmd/tracebench-sample` indexes merged `peasant-labs/peasant` pull requests,
links them to agent sessions recorded in a local Peasant database (head-ref
name matches, same-issue open windows, and commit associations; a session may
link to several pull requests), closes over parent/child/fork session lineage
so bundles carry their full session forest, and samples a time- and
size-stratified train/val/test corpus with the raw transcripts:

```bash
go run ./cmd/tracebench-sample index
go run ./cmd/tracebench-sample sample --train 30 --val 10 --test 9
go run ./cmd/tracebench-sample dump
```

Indexes land in `corpus/index/`, the sampled dataset in `corpus/dataset/`, and
`dump/` holds a flat, publishable dump: `metadata.jsonl` records in
`schema.UnifiedMetadata`, `transcripts/` envelopes in
`schema.TranscriptContent` (`session_detail`), plus `pull_requests.jsonl` and
`traces.jsonl` indexes. Transcripts pass through the `redact` pipeline at the
standard level, metadata omits machine-specific paths, and `--pin-prs FILE`
reproduces a previous sample. `--source village-pull` builds the same dump
from `peasant village pull` directories instead of the local database, with
collective provenance in `village_pulls.jsonl`. `corpus/` is ignored by git.
Sessions whose raw source is OpenCode's monolithic database are exported per
entry from the Peasant full-content capture, and the manifests flag missing or
partial transcripts.

The published corpus lives at
[huggingface.co/datasets/dayvidpham/TraceBench](https://huggingface.co/datasets/dayvidpham/TraceBench);
the viewer's `session_pr_traces` table is the session-to-PR mapping
(`pr`, `session_id`, `method`, `relation`, `split`). Consumers pull it with:

```bash
go run ./cmd/tracebench-sample fetch --dest data/tracebench
```

`fetch` downloads `metadata.jsonl`, the indexes, and every transcript, and
verifies each transcript's SHA3-256 against its metadata `contentHash`
(`--no-verify` to skip, `--revision` to pin a revision).

A Python loader for Harbor task environments and verifiers lives in
[`loaders/`](loaders/README.md): it loads the dump (local or directly from
HuggingFace), joins traces, PRs, metadata, and transcript turns, materializes
self-contained per-PR bundles, assembles task payloads (PR + prior traces up
to the PR's develop boundary + integration points for the pre-PR repo and
merged-state golden tests), adapts prior context to a target
harness/model/thinking configuration, and generates Harbor task skeletons
from those payloads.

## Verification status

* Test logic: buggy code fails 6/7 grading tests, oracle-fixed code passes 7/7.
* `harbor run -p tasks/trace-propagation -a oracle -e docker` → reward `1.0`
  (mechanics verified with policy temporarily relaxed to `public`, see below).

> macOS: Docker Desktop's VM kernel lacks `CONFIG_NFT_FIB_INET`, so Harbor
> correctly **fails closed** (`no-network is not supported`) instead of running
> with weaker isolation. **Use Podman locally** — its VM kernel passes Harbor's
> probe and `no-network` enforces correctly (verified: oracle reward `1.0`,
> raw-socket HTTP probes return 0 bytes):
>
> ```bash
> brew install podman
> podman machine init && podman machine start
> harbor run -p tasks/trace-propagation -a oracle -e podman
> ```
>
> Linux Docker and cloud sandboxes (`daytona`, `e2b`, `modal`, …) also enforce
> strictly. To smoke-test mechanics only with Docker Desktop, use a relaxed
> copy — keep the committed `task.toml` strict:
>
> ```bash
> rm -rf /tmp/tb-public && cp -r tasks/trace-propagation /tmp/tb-public
> sed -i '' 's/network_mode = "no-network"/network_mode = "public"/' /tmp/tb-public/task.toml
> harbor run -p /tmp/tb-public -a oracle -e docker
> ```

## Roadmap

* [ ] Separate verifier sandbox (`[verifier.environment_mode = "separate"]`)
* [ ] `allowlist` variant (e.g. PyPI-only) for tasks that need installs at run time
* [ ] Publish dataset to Harbor Hub (`harbor publish`), hosted jobs + leaderboard
* [ ] More tasks (vendored real-bug SWE-style, multi-step)
