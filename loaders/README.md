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
  --adaptations loaders/tests/testdata/adaptations.yaml \
  --adaptation claude-code-sonnet-high \
  --dest task-343
```

| path | contents |
|---|---|
| `pr.json` | the pull request, enriched from `--index` (`merge_commit`, `head_oid`, `base_ref`) |
| `prior-traces/` | `traces.jsonl`, `metadata.jsonl`, `transcripts/`, and `manifest.json` for the prior context (below) |
| `repo/` | **integration point**: the working tree at the pre-PR state; empty until the repository tooling fills it |
| `tests/` | **integration point**: every test file at the merged state (the golden suite); empty until filled |
| `repo-request.json` | the contract for the repository tooling: `tree_commit`, `trace_cutoff`, test patterns, glob dialect, merge-commit policy |
| `task.json` | payload summary: cutoff, adaptation, counts, exclusions |

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
- **Adaptation.** When `--adaptation NAME` selects an entry from
  `--adaptations SPEC`, only prior sessions whose harness, model, and thinking
  level match the entry are kept; the rest are recorded as
  `excluded_by_adaptation`. The adaptation is recorded in the payload and in a
  generated skeleton's `task.toml` metadata.

### Adaptations

An adaptation spec names the harness, model, and thinking level a task run
targets (thinking is one of `none`, `low`, `medium`, `high`, `xhigh`):

```yaml
adaptations:
  - name: claude-code-sonnet-high
    harness: claude-code
    model: claude-sonnet-4-6
    thinking: high
```

`load_adaptations(path)` validates the spec (unique names, closed thinking
set, known keys); `Adaptation.matches(metadata)` applies the axes. The corpus
contract does not carry a thinking level, so matching derives it: declared
`thinkingLevel` metadata matches exactly, and otherwise a session with
`stats.thoughtTokens` counts as thinking and one without as `none`.

### Destination hygiene

`task` and `skeleton` refuse a non-empty destination. `--force` clears exactly
the tool-owned entries (`prior-traces/`, `repo/`, `tests/`, `repo-request.json`,
`pr.json`, `task.json`; the task directory's `environment/`, `tests/`,
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
| `tests/golden/` | the merged-state test suite, verifier-only (Harbor copies `tests/` to `/tests` for the verifier; the agent never sees it) |
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
