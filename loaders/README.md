# TraceBench corpus loader

Python loader for the flat dump produced by `tracebench-sample dump` /
`fetch` (published at [dayvidpham/TraceBench](https://huggingface.co/datasets/dayvidpham/TraceBench)).
It loads the session-to-PR mapping, pull requests, `schema.UnifiedMetadata`
records, and `schema.TranscriptContent` transcripts, materializes
self-contained per-PR bundles, assembles task payloads, and generates Harbor
task skeletons from them.

## Install

```bash
uv run --directory loaders --extra test pytest   # run the tests
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
| `repo/` | **integration point**: the working tree at the pre-PR state; empty until the repository tooling fills it |
| `tests/` | **integration point**: every test file at the merged state; empty until filled |
| `test-manifest.json` | with `--repo-dir`, every merged-state Go test file and case, with PR-changed cases tagged `golden` and all cases initially `accept` |
| `repo-request.json` | the contract for the repository tooling: `tree_commit`, `trace_cutoff`, test patterns, glob dialect, merge-commit policy |
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
- **Family.** The codebase family pairs `peasant-labs/peasant` with
  `peasant-labs/peasant-prerelease-archive`, so live tasks see archive traces
  as prior context.
- **Exclusions.** The pull request's own sessions are never prior context, and
  prior sessions whose end time is after the boundary are cut (recorded as
  `sessions_past_cutoff`).
- **Completeness.** Sessions without a transcript or metadata are recorded as
  `missing_sessions`; the payload distinguishes selected from materialized
  sessions.
- **Scope.** Prior traces cover the sampled pull requests only; the corpus is a
  sample, not the repository's full history.
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
case starts with `status: "accept"`; a future counting policy may change a
case to `reject` without changing its golden classification.

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
holds the full merged-state suite, while the manifest's `golden` flags identify
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
  --base-image tracebench/peasant-base:latest
```

| path | contents |
|---|---|
| `task.toml` | registry-safe name (`<org>/<repo-slug>-pr-<number>`), PR metadata, `[environment].docker_image` (the shared base image) and `workdir`, offline network policy for agent and verifier |
| `instruction.md` | scaffolded from the pull request; task authors replace the TODO with the issue description |
| `environment/repo/`, `environment/prior-traces/` | task data, uploaded into the container workdir at environment start; **no per-task image is built** |
| `tests/golden/` | the full merged-state test suite, verifier-only (Harbor copies `tests/` to `/tests` for the verifier; the agent never sees it) |
| `tests/test-manifest.json` | verifier-only copy of the manifest when the payload has one |
| `tests/test.sh` | removes pre-PR test files matching the payload's test patterns, overlays the golden suite, runs it, writes `/logs/verifier/reward.txt`; fails closed when golden tests are missing |
| `solution/solve.sh` | oracle placeholder (exits non-zero until implemented) |

The oracle and the test command are explicit TODO placeholders: a skeleton is
structure, not a solvable task, until a task author fills them in. Task names
are registry-safe and distinguish the archive (`peasant-archive-pr-0021`) from
the live repository (`peasant-pr-0021`).

Python API: `build_skeleton(payload_dir, dest, base_image=..., workdir=..., force=...)`.

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
├── solution/solve.sh
└── tests/
    ├── golden/               # verifier-only merged-state suite
    └── test.sh
```

The base image provides the toolchain; the task data arrives at environment
start, and the golden suite at verification time. A materialized payload
directory is also a valid mini corpus (`load_corpus(path="task-343/prior-traces")`
reads its `traces.jsonl` and `metadata.jsonl`).

The loader is stdlib-only; `huggingface_hub` is needed only for
`load_corpus(repo=...)`, and `PyYAML` only for YAML adaptation specs.
