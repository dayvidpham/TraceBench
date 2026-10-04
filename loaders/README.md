# TraceBench corpus loader

Python loader for the flat dump produced by `tracebench-sample dump` /
`fetch` (published at [dayvidpham/TraceBench](https://huggingface.co/datasets/dayvidpham/TraceBench)).
It loads the session-to-PR mapping, pull requests, `schema.UnifiedMetadata`
records, and `schema.TranscriptContent` transcripts, materializes
self-contained per-PR bundles, assembles task payloads, and generates Harbor
task skeletons from them. The `pipeline` command chains every step and builds
runnable Harbor tasks from a list of pull requests.

## Install

The repository root is a uv workspace with this package as its only member.

```bash
# From the repository root:
uv sync                          # workspace environment, dev tools included
uv run pytest                    # run the loader tests
uv run tracebench-corpus --help  # the CLI

# From loaders/, the member's extras also work:
uv run --directory loaders --extra test pytest
uv run --directory loaders --extra hf python     # REPL with HuggingFace loading
```

Or install into any environment: `pip install ./loaders[hf]`.

## API

```python
from tracebench_corpus import load_corpus

corpus = load_corpus(path="corpus/dump")            # local dump
corpus = load_corpus(repo="dayvidpham/TraceBench")  # or straight from HuggingFace

corpus.prs()                         # sampled pull requests with splits
corpus.splits()                      # split -> PR ids
corpus.sessions_for_pr("peasant-labs/peasant#343")

bundle = corpus.pr_bundle("peasant-labs/peasant#343")
bundle.pr                            # PR record (title, url, additions, split, ...)
bundle.traces                        # session-PR rows (method, relation, split)
bundle.metadata                      # UnifiedMetadata record per session
bundle.turns("session-id")           # transcript turns for one session
bundle.missing_sessions              # sessions whose transcript is absent

corpus.materialize("peasant-labs/peasant#343", "bundle/pr-0343")
corpus.materialize_all("bundles", split="train")
```

## CLI

```bash
tracebench-corpus --corpus corpus/dump list --split train
tracebench-corpus --repo dayvidpham/TraceBench bundle "peasant-labs/peasant#343" --dest bundle/pr-0343
tracebench-corpus --corpus corpus/dump bundle-all --dest bundles --split test
tracebench-corpus --corpus corpus/dump task "peasant-labs/peasant#343" --dest task-343 \
  --index corpus/index/merged_prs.json --repo-dir /path/to/clone --materialize-tests
tracebench-corpus oracle "peasant-labs/peasant#343" --payload task-343 \
  --repo-dir /path/to/clone --spec repository_specs.yaml   # or --build-command CMD
tracebench-corpus skeleton --payload task-343 --dest tasks/pr-0343
tracebench-corpus test-manifest --repo-dir /path/to/clone \
  --base-commit BASE_SHA --merge-commit MERGE_SHA --dest test-manifest.json
tracebench-corpus --corpus corpus/dump pipeline --prs prs.txt \
  --repo-dir /path/to/clone --index corpus/index/merged_prs.json \
  --dest build/run-1 --run-id tracebench-smoke-1
```

## Task payloads

`task` assembles the payload for one benchmark task: the pull request, the
traces of the codebase merged before the pull request's develop boundary, and
integration points for the repository tooling.

```bash
tracebench-corpus --corpus corpus/dump task "peasant-labs/peasant#343" \
  --index corpus/index/merged_prs.json \
  --repo-dir /path/to/peasant-clone \
  --target-configs loaders/tests/testdata/target_configurations.yaml \
  --target-config claude-code-sonnet-high \
  --dest task-343
```

| path | contents |
|---|---|
| `pr.json` | the pull request, enriched from `--index` (`merge_commit`, `head_oid`, `base_ref`) |
| `prior-traces/` | `traces.jsonl`, `metadata.jsonl`, `transcripts/`, and `manifest.json` for the prior context (below) |
| `repo/` | the working tree at the pre-PR state (`tree_commit`); materialized by `worktree.materialize_worktree`, which verifies the result against `tree_commit^{tree}` |
| `tests/` | with `--materialize-tests` (requires `--repo-dir`), every file at `merge_commit` matching the payload's test patterns, read from the git object database (executable bits kept, symlinks skipped); empty otherwise. Zero matches, or a root-level `manifest.json` colliding with the manifest, fail closed |
| `tests/manifest.json` | the extracted golden suite: `commit`, `patterns`, sorted `paths` |
| `solution/` | written by `oracle`: `oracle.patch` (the merge diff) and `solve.sh` (apply the patch, run the build command), verified to reproduce the merged tree |
| `test-manifest.json` | with `--repo-dir`, the **case catalog**: every merged-state Go test file and case, with PR-changed cases tagged `golden` and all cases initially `accept`. Distinct from `tests/manifest.json`, which lists the extracted files |
| `repo-request.json` | the contract for the repository tooling: `tree_commit`, `trace_cutoff`, `test_patterns` (read by extraction and `tests/test.sh`; defaults apply when absent), glob dialect, merge-commit policy |
| `task.json` | payload summary: cutoff, target configuration, counts, exclusions |

### Prior context rules

- **Boundary.** `develop` keeps one commit per pull request (squash-rebase), so
  "before the PR" is the parent of the PR's squash commit. With `--repo-dir`
  the boundary is resolved by commit ancestry (`merge_commit^`); pull requests
  of the target's repository are selected as ancestors of that boundary, while
  family counterparts (the prerelease archive, a separate git history) are
  ordered by `merged_at`. Without `--repo-dir`, `merged_at` is the portable
  proxy for everything. The chosen basis is recorded as `cutoff.basis` in both
  manifests. A merge commit is required: pass `--index` when the published dump
  record lacks one.
- **Exclusions.** The pull request's own sessions are never prior context, and
  prior sessions whose end time is after the boundary are cut (recorded as
  `sessions_past_cutoff`).
- **Completeness.** Sessions without a transcript or metadata are recorded as
  `missing_sessions`; the payload distinguishes selected from materialized
  sessions.
- **Scope.** Prior traces cover the sampled pull requests only; the corpus is a
  sample, not the repository's full history. Prior context comes from the same
  repository: the prerelease archive is frozen pre-launch history and provides
  neither tasks nor context (an archive target fails closed).
- **Target configuration.** `--target-config NAME` selects an entry from
  `--target-configs SPEC`. The payload records the harness, model, and
  thinking level for the runner. The configuration does **not** filter prior
  context: the model receives every prior trace, independent of the
  configuration. A generated skeleton's `task.toml` metadata carries the
  configuration name.

### Test manifest

When `--repo-dir` is supplied, task generation compares the merged commit to
its first parent and writes `test-manifest.json`. It includes every Go
`*_test.go` file at the merged commit, with top-level test functions tagged
`golden: true` only when their function was added or changed by the PR. Every
case starts with `status: "accept"`, except cases in files whose `//go:build`
constraint the test command does not satisfy (the command runs without custom
tags; the context is linux/amd64 with `cgo`/`gc`/`unix`): those carry
`status: "reject"` with a reason, and the verifier excludes them from the
reward denominator. Golden classification is independent of the status.

```json
{
  "schema_version": 1,
  "base_commit": "...",
  "merge_commit": "...",
  "case_granularity": "top-level Go test functions; runtime subtests are not enumerated",
  "summary": {"suites": 1, "cases": 2, "golden_cases": 1},
  "suites": [{
    "path": "internal/api/sync_test.go",
    "framework": "go",
    "package_dir": "internal/api",
    "golden": true,
    "cases": [
      {"id": "internal/api/sync_test.go::TestExisting", "name": "TestExisting", "golden": false, "status": "accept"},
      {"id": "internal/api/sync_test.go::TestNew", "name": "TestNew", "golden": true, "status": "accept"}
    ]
  }]
}
```

For a standalone manifest, run:

```bash
tracebench-corpus test-manifest --repo-dir /path/to/peasant-clone \
  --base-commit BASE_SHA --merge-commit MERGE_SHA \
  --dest test-manifest.json
```

Removed tests are absent because the inventory represents the merged suite.
Dynamic subtests and fixture-driven cases require runtime discovery through
`go test -json`; the static manifest inventories top-level Go functions. The
current extractor does not classify JavaScript or TypeScript test cases.
Without `--repo-dir`, task generation cannot classify PR changes and does
not write a manifest.
The existing `tests/golden/` directory name predates this classification: it
holds the extracted merged-state suite, while the manifest's `golden` flags identify
the PR-added or PR-modified cases within it.

### Target configurations

A target configuration names the harness, model, and thinking level that a
benchmark run uses (thinking is one of `none`, `low`, `medium`, `high`,
`xhigh`):

```yaml
target_configurations:
  - name: claude-code-sonnet-high
    harness: claude-code
    model: claude-sonnet-4-6
    thinking: high
```

`load_target_configs(path)` validates the spec (unique names, closed thinking
set, known keys) and `find_target_config(configs, name)` selects one entry.
The record always carries all three axes; a missing axis is `null` (stubbed).
The corpus does not carry a thinking level yet; the upstream work is tracked in
`peasant-labs/schema#147` (schema field) and `peasant-labs/peasant#545`
(populate at ingest).

### Destination hygiene

`task` and `skeleton` refuse a non-empty destination. `--force` clears exactly
the tool-owned entries (`prior-traces/`, `repo/`, `tests/`, `repo-request.json`,
`pr.json`, `task.json`, `test-manifest.json`; the task directory's `environment/`, `tests/`,
`solution/`, `task.toml`, `instruction.md`, `task-payload.json`) and rebuilds
them; caller-authored files elsewhere are never touched.

## Harbor task skeletons

`skeleton` turns a task payload into a Harbor task directory:

```bash
tracebench-corpus skeleton --payload task-343 --dest tasks/pr-0343 \
  --base-image tracebench/task-runtime:latest
```

| path | contents |
|---|---|
| `task.toml` | registry-safe name (`<org>/<repo-slug>-pr-<number>`), PR metadata, `[environment].docker_image` (the shared base image) and `workdir`, offline network policy for agent and verifier, and the environment healthcheck that warms the pre-PR dependency and build caches while the environment network is still up (the agent and verifier phases stay offline) |
| `instruction.md` | scaffolded from the pull request; task authors replace the TODO with the issue description |
| `environment/repo/`, `environment/prior-traces/` | task data, uploaded into the container workdir at environment start; **no per-task image is built** |
| `tests/golden/` | the payload's extracted merged-state test suite, verifier-only (Harbor copies `tests/` to `/tests` for the verifier; the agent never sees it) |
| `tests/manifest.json` | verifier-side golden-suite manifest (commit, patterns, paths) |
| `tests/test-manifest.json` | verifier-only copy of the case catalog when the payload has one |
| `tests/verifier-config.json` | the PR id, the test command, the test patterns, and the removal regexes compiled from them by the canonical doublestar matcher |
| `tests/verifier.py` | the standard-library verifier, copied from `tracebench_corpus/verifier.py` |
| `tests/repo-request.json` | the payload's request, shipped unchanged for provenance |
| `tests/test.sh` | runs `python3 /tests/verifier.py run`: removes the pre-PR test files, overlays the golden suite, runs the test command, writes `/logs/verifier/reward.txt` (`passed / accepted cases`; rejected cases are excluded) and `/logs/verifier/test-results.json`. A crashed verifier writes reward 0 |
| `solution/` | the payload's `solution/` (oracle patch and generated `solve.sh`) when `oracle` has run; otherwise a placeholder `solve.sh` that exits non-zero |

Without `oracle` output the oracle is an explicit placeholder: such a skeleton
is structure, not a solvable task. Task names
are registry-safe and distinguish the archive (`peasant-archive-pr-0021`) from
the live repository (`peasant-pr-0021`).

Python API: `build_skeleton(payload_dir, dest, base_image=..., workdir=..., test_command=..., force=...)`.

## Harbor integration

Generate the payload and skeleton, then complete the task:

```bash
tracebench-corpus --corpus corpus/dump task "peasant-labs/peasant#343" \
  --index corpus/index/merged_prs.json --dest task-343
tracebench-corpus skeleton --payload task-343 --dest tasks/pr-0343
```

```text
tasks/pr-0343/
├── task.toml                 # docker_image + workdir + network policy
├── instruction.md
├── environment/
│   ├── repo/                 # uploaded to <workdir>/repo at start
│   └── prior-traces/         # uploaded to <workdir>/prior-traces at start
├── solution/
│   ├── oracle.patch          # when the oracle has run
│   └── solve.sh
└── tests/
    ├── golden/               # verifier-only merged-state suite
    ├── manifest.json         # extracted golden paths
    ├── test-manifest.json    # case catalog
    ├── verifier-config.json
    ├── verifier.py
    ├── repo-request.json
    └── test.sh
```

The base image provides the toolchain; the task data arrives at environment
start, and the golden suite at verification time. A materialized payload
directory is also a valid mini corpus (`load_corpus(path="task-343/prior-traces")`
reads its `traces.jsonl` and `metadata.jsonl`).

The loader is stdlib-only; `huggingface_hub` is needed only for
`load_corpus(repo=...)`, and `PyYAML` only for YAML adaptation specs.

## Pipeline

`pipeline` builds one runnable Harbor task per pull request and one Harbor job
config for the run. It never runs Harbor.

```bash
tracebench-corpus --corpus corpus/dump pipeline \
  --prs "peasant-labs/peasant#343" --prs more-prs.txt \
  --repo-dir /path/to/peasant-clone \
  --index corpus/index/merged_prs.json \
  --dest build/run-1 \
  [--spec repository_specs.yaml] \
  [--run-id ID | --target-configs SPEC --target-config NAME [--run-label LABEL]] \
  [--job-config-format yaml|json] [--force]
```

| flag | meaning |
|---|---|
| `--prs` | a pull request id (`owner/repo#N`) or a file with one id per line; repeatable; duplicates keep the first position |
| `--repo-dir` | local clone that holds `tree_commit` and `merge_commit` of every pull request |
| `--index` | `corpus/index/merged_prs.json`, the source of `merge_commit` |
| `--dest` | output directory: `payloads/`, `tasks/`, and the job config |
| `--spec` | repository adaptation spec; the shipped Go/Peasant default when absent |
| `--run-id` | the run id; when absent it is derived from the target configuration |
| `--target-configs`, `--target-config` | the target configuration; together, or not at all |
| `--run-label` | label of a derived run id (default `tracebench-<configuration name>`) |
| `--job-config-format` | `yaml` (default when PyYAML is installed) or `json` |
| `--force` | rebuild existing payload and task directories |

A target may be a merged pull request that the published dump does not sample
(for example, one with no traced sessions): `TaskBuilder.resolve_pull_request`
resolves it from `--index`, and prior context still comes from the sampled
corpus. Only a pull request absent from both the corpus and the index fails
closed.

For each pull request, in order:

1. **payload** — `TaskBuilder.build` with the golden suite materialized:
   `pr.json`, `prior-traces/`, `repo-request.json`, `tests/` +
   `tests/manifest.json`, `test-manifest.json`, `task.json`.
2. **`environment/repo`** — `materialize_worktree` writes `repo/` at
   `tree_commit` (below).
3. **`solution/oracle.patch`** — `build_oracle` + `write_oracle` with the
   spec's build command.
4. **task** — `build_skeleton` with the spec's test command, then a check that
   `task.toml`, `environment/repo`, `tests/golden`, `tests/test.sh`, and
   `solution/oracle.patch` exist.

A failure fails that task only and names the part (`payload`, `tests/golden`,
`environment/repo`, `solution/oracle.patch`, `task`, or the first missing
path). The other tasks still run. The summary prints one line per task, and the
exit code is 1 when any task failed (2 when the run cannot start).

Output layout (`<name>` is `<slug>-pr-NNNN`, for example `peasant-pr-0343`):

```text
<dest>/
├── payloads/<name>/     # task payload
├── tasks/<name>/        # Harbor task
└── job-config.yaml      # or job-config.json
```

The job config:

```yaml
job_name: <run id>
n_attempts: 3
tasks:
  - path: <dest>/tasks/<name>      # absolute; only the tasks that were built
    source: <run id>
agents:
  - name: oracle                   # or the configuration's harness
    model_name: null               # or the configuration's model
    kwargs: {}                     # {reasoning_effort: <thinking>} when set
    env: {TRACEBENCH_RUN_ID: <run id>}
verifier:
  env: {TRACEBENCH_RUN_ID: <run id>}
```

A derived run id is `<label>-<12 hex>`: the first 12 hex digits of
`sha256(harness|provider|model|thinking|revision)`, where `provider` is the
`provider/` prefix of the model name and `revision` hashes the ordered pull
request ids with their merge commits. The same configuration and the same
pull request list give the same run id.

Verify a built task with Harbor (when Harbor is installed):

```bash
harbor run -p build/run-1/tasks/peasant-pr-0343 -a oracle -e podman   # reward 1.0
harbor run -c build/run-1/job-config.yaml
```

### Modules

| module | role |
|---|---|
| `cli.py` | the `tracebench-corpus` commands: `list`, `bundle`, `bundle-all`, `task`, `skeleton`, `test-manifest`, `oracle`, `pipeline` |
| `corpus.py` | loads a local or HuggingFace dump; per-PR bundles and transcript turns |
| `task.py` | payload assembly: develop boundary, prior traces, session cuts, `repo-request.json`, destination hygiene |
| `golden.py` | golden suite: files at `merge_commit` matching the test patterns, `tests/manifest.json`; owns the canonical doublestar matcher (`glob_to_regex`) |
| `test_manifest.py` | case catalog: top-level Go test cases at `merge_commit`, PR-changed cases flagged `golden` |
| `worktree.py` | secure worktree: packs the ancestry of `tree_commit` into a fresh repo; asserts HEAD, tree, source-equal count, not shallow, no remotes, fix absent, clean `fsck` |
| `oracle.py` | oracle: merge diff, `solve.sh`, equivalence check, the `task.json` `oracle` block |
| `repository_spec.py` | repository adaptation spec: test and build command per repository |
| `target_config.py` | target configurations: harness, model, thinking level |
| `skeleton.py` | Harbor task directory, `test.sh`, verifier shipping |
| `verifier.py` | the verifier that runs inside the task (standard library only) |
| `pipeline.py` | the batch driver and the Harbor job config |

### Secure worktree

`materialize_worktree(repo_dir, payload_dir)` reads
`tree_commit` from `repo-request.json` (falling back to `merge_commit^`) and
writes `repo/` as the project's **full real history truncated at the pre-PR
commit**: every ancestor of `tree_commit` with its real SHA, and nothing at or
after the PR. The PR's base branch comes from the payload's `base_ref`
(`refs/heads/` stripped), defaulting to `main`.

```bash
git -C <clone> pack-objects --revs --stdout > history.pack   # stdin: <tree_commit>
git init -q repo
git -C repo config core.logAllRefUpdates false
git -C repo index-pack --stdin < history.pack
git -C repo update-ref refs/heads/<base_ref> <tree_commit>
git -C repo symbolic-ref HEAD refs/heads/<base_ref>
git -C repo reset --hard -q
git -C repo config core.logAllRefUpdates true
rm -rf repo/.git/logs repo/.git/ORIG_HEAD repo/.git/FETCH_HEAD
```

A local `user.name`/`user.email` (`TraceBench Agent <agent@tracebench.local>`)
is set so the agent can commit, and no remote is configured. The source clone
is only read; its refs and HEAD are unchanged.

The worktree fails closed, naming the commit and the failing check, when the
commit does not exist, the destination is not empty, HEAD is not the real
`tree_commit`, the HEAD tree differs from `git rev-parse <tree_commit>^{tree}`,
`git rev-list --all --count` differs from the source's count for that commit
(or is not greater than 1), `.git/shallow` exists, a remote exists, the PR's
`merge_commit` is in the object store, `.git/logs`/`ORIG_HEAD`/`FETCH_HEAD`/
`objects/info/alternates` exist, the worktree is not clean, `git fsck --full`
reports anything, or the local identity is wrong. Errors are `WorktreeError`
(a `ValueError`).

The default task image is `tracebench/task-runtime:latest`
(`tasks/_base/task-runtime.Dockerfile`): the shared toolchain base without the
peasant clone or the Go build cache, with `/workdir` present.

### Oracle

`build_oracle(repo_dir, tree_commit, merge_commit, build_command)` takes
`git diff <tree_commit> <merge_commit>`, applies it in a temporary detached
worktree at `tree_commit` (`git apply --check`, then `git apply --index`), and
compares the written tree with `merge_commit^{tree}`. A mismatch fails closed
and names both trees. `write_oracle` writes `solution/oracle.patch`,
`solution/solve.sh` (apply the patch to `/workdir/repo`, then run the build
command; non-zero on failure), and the `oracle` block of `task.json`
(`patch`, `tree_commit`, `merge_commit`, `changed_files`, `applied_tree`,
`verified`).

### Verifier

`tests/test.sh` runs `verifier.py` inside the task image, with no network:

1. Delete the files under `/workdir/repo` that match `remove_regexes` from
   `verifier-config.json`.
2. Overlay `/tests/golden/` into `/workdir/repo`.
3. Run the test command (`go test -json -count=1 ./...` by default; `-count=1`
   disables the test cache).
4. Parse the `go test -json` stream: keep events with a non-empty `Test` and no
   `/` in the name; the last event per test decides (`pass`, `fail`, `skip`).
5. Join to `test-manifest.json` on `(package_dir, test name)`; `package_dir`
   is the `Package` import path minus the module path from `go.mod` (`.` for
   the root).
6. Write `/logs/verifier/reward.txt` = `passed / total`, where `total` is the
   manifest's `summary.cases`.
7. Write `/logs/verifier/test-results.json`.

Fail-closed rules: a missing manifest, an unknown manifest `schema_version`,
an unparsable stream line, a killed test command (timeout or signal), a
truncated stream, or `total` of zero gives reward 0 and records the reason in
`fail_closed_reasons`. The exit code of the test command never decides the
reward. A manifest whose case list disagrees with `summary.cases` also gives
reward 0. A case with outcome `fail`, `skip`, or `missing` counts as not
passed: a package build failure and an unknown final action are `fail`, and a
case with no final action is `missing`. Exactly one reward file is written;
there is no `reward.json`.

`test-results.json` carries `schema_version`, `pr`, `base_commit`,
`merge_commit`, `manifest_schema_version`, `test_command`, `exit_code`,
`duration_sec`, the counts (`total_cases`, `passed`, `failed`, `skipped`,
`missing`, `rejected_cases`, `golden_flagged`, `golden_flagged_passed`),
`reward` (equal to `reward.txt`), `fail_closed_reasons`, `failed_test_ids`
(outcome `fail` only), `rejected_test_ids`, and `entries` (`id`,
`package_dir`, `name`, `golden`, `status`, `outcome` in the closed set
`pass`, `fail`, `skip`, `missing`, `reject`). Rejected cases are excluded from
`total_cases` and the reward. Harbor downloads it to
`<trial-dir>/verifier/test-results.json`. The `golden` flag is diagnostic; the
reward does not use it.

## How to modify

### Repository adaptation spec

The spec names, per repository, the test framework, the test command (run by
the verifier), and the build command (run by `solve.sh`). It is YAML when
PyYAML is installed, JSON otherwise:

```yaml
repositories:
  - match: peasant-labs/peasant
    framework: go
    test_command: "go test -json -count=1 ./..."
    build_command: "go build ./..."
  - match: peasant-labs/peasant-prerelease-archive
    framework: go
    test_command: "go test -json -count=1 ./..."
    build_command: "go build ./..."
```

- `match` is the exact `owner/name` of the pull request's repository; the first
  match wins, and a duplicate `match` is an error. A repository with no entry
  fails that task and lists the known matches.
- The keys are closed: `match`, `framework`, `test_command`, `build_command`,
  all non-empty strings.
- With no `--spec`, `DEFAULT_REPOSITORY_SPECS` in `repository_spec.py` applies
  (the spec above). Change the default there; test fixtures live in
  `tests/testdata/repository_specs.yaml`.
- `oracle --spec FILE` reads the build command from the same spec;
  `--build-command CMD` overrides it for one payload.

**Adding a language.** A spec entry alone is not enough today: the case
catalog (`test_manifest.py`) inventories Go test functions only, and the
verifier (`verifier.py`) parses only the `go test -json` stream and joins on
Go package directories. The `framework` field is recorded but not yet used to
select a parser. A new language needs a case extractor in `test_manifest.py`,
a report parser and join key in `verifier.py`, its test patterns, a spec entry
with a test command that emits the parsed format, and a base image with the
toolchain.

### Target configurations

`--target-configs SPEC --target-config NAME` selects the harness, model, and
thinking level (see [Target configurations](#target-configurations)). In the
pipeline, the configuration fills `agents[0]` of the job config (`name` =
harness, `model_name` = model, `kwargs.reasoning_effort` = thinking) and
derives the run id; without one, the job config uses the `oracle` agent and
`--run-id` is required. A configuration used for a job config must name a
harness.

### Test patterns

The test patterns decide which files form the golden suite and which pre-PR
files the verifier deletes. They come from `repo-request.json`
(`test_patterns`), with `DEFAULT_TEST_PATTERNS` as the fallback. Patterns are
doublestar globs relative to the repository root; `**` matches only a whole
path segment. `golden.glob_to_regex` is the one matcher: extraction uses it,
and the skeleton compiles the removal regexes with it into
`verifier-config.json`, so extraction and deletion never disagree. Matcher
cases live in `tests/testdata/glob_patterns.yaml`.

### Verifier contract

The reward is the fraction `passed / total`, written as one float to
`/logs/verifier/reward.txt`; the per-case report is
`/logs/verifier/test-results.json` (see [Verifier](#verifier)). The verifier
must stay standard-library only, because it is copied into each task and runs
with the image's `python3`. Change the parser, the fail-closed rules, or the
report in `verifier.py`; the stream fixtures live in
`tests/testdata/go_test_json/`. A change to the `test-results.json` fields
bumps its `schema_version`, because the eval pipeline reads it.

### Skeleton layout

`skeleton.py` owns the task layout: `task.toml` (base image, workdir,
timeouts, network policy), `instruction.md`, `environment/`, `tests/`, and
`solution/`. `--force` clears only the entries in `SKELETON_ENTRIES`. The
verifier timeout passed to `test.sh` leaves headroom under Harbor's verifier
timeout so a hung test run is recorded as killed.

### Where each artifact lives

| artifact | payload | task | in the container |
|---|---|---|---|
| pre-PR tree | `repo/` | `environment/repo/` | `/workdir/repo` |
| prior traces | `prior-traces/` | `environment/prior-traces/` | `/workdir/prior-traces` |
| golden suite | `tests/` | `tests/golden/` | `/tests/golden/` (verifier only) |
| extracted paths | `tests/manifest.json` | `tests/manifest.json` | `/tests/manifest.json` |
| case catalog | `test-manifest.json` | `tests/test-manifest.json` | `/tests/test-manifest.json` |
| verifier config | — | `tests/verifier-config.json` | `/tests/verifier-config.json` |
| oracle | `solution/oracle.patch`, `solution/solve.sh` | `solution/` | oracle runs only |
| reward | — | — | `/logs/verifier/reward.txt` |
| per-case report | — | — | `/logs/verifier/test-results.json` |
| job config | — | — (one per run: `<dest>/job-config.yaml`) | — |
