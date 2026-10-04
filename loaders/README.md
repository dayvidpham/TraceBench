# TraceBench corpus loader

Python loader for the flat dump produced by `tracebench-sample dump` /
`fetch` (published at [dayvidpham/TraceBench](https://huggingface.co/datasets/dayvidpham/TraceBench)).
It loads the session-to-PR mapping, pull requests, `schema.UnifiedMetadata`
records, and `schema.TranscriptContent` transcripts, and materializes
self-contained per-PR bundles for use inside Harbor task environments and
verifiers.

## Install

```bash
uv run --directory loaders --extra test pytest   # run the tests
uv run --directory loaders --extra hf python     # REPL with HuggingFace loading
```

Or install into any environment: `pip install ./loaders[hf]`.

## API

```python
from tracebench_corpus import load_corpus

corpus = load_corpus(path="corpus/dump")          # local dump
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
traces of the codebase merged before it (excluding the PR's own sessions), and
integration points for the repository tooling.

```bash
tracebench-corpus --corpus corpus/dump task "peasant-labs/peasant#343" \
  --index corpus/index/merged_prs.json --dest task-343
```

| path | contents |
|---|---|
| `pr.json` | the pull request; enriched with `merge_commit`, `created_at`, and `base_ref` when `--index` is passed |
| `prior-traces/` | `traces.jsonl`, `metadata.jsonl`, `transcripts/`, and `manifest.json` for every pull request of the same codebase family merged before this PR's start, excluding this PR's own sessions |
| `repo/` | **integration point**: the working tree at the pre-PR state; empty until the repository tooling fills it |
| `tests/` | **integration point**: every test file at the merged state (the golden suite); empty until filled |
| `repo-request.json` | exactly what the repository tooling must materialize: repo, merge commit, base-commit rule, cutoff, and test patterns |
| `task.json` | payload summary (cutoff, prior trace/session/PR counts) |

The codebase family pairs `peasant-labs/peasant` with
`peasant-labs/peasant-prerelease-archive`, so a live task sees archive traces
as prior context. The repository integration point matches the `snapshot/`
module (issue #2), which resolves the same PR cutoff and materializes
repository trees.

Python API: `TaskBuilder(corpus, pr_index=...).build(pr_id, dest)`.

## Harbor task skeletons

`skeleton` turns a task payload into a Harbor task directory:

```bash
tracebench-corpus skeleton --payload task-343 --dest tasks/pr-0343
```

| path | contents |
|---|---|
| `task.toml` | task name (`<org>/<repo-slug>-pr-<number>`), metadata (PR url/number/split), timeouts, network policy (agent and verifier offline, environment baseline public) |
| `instruction.md` | scaffolded from the pull request; task authors replace the TODO with the issue description |
| `environment/Dockerfile` | base image; copies `repo/` to `/app` and `prior-traces/` to `/prior-traces`; TODO for the repository toolchain |
| `environment/repo/`, `environment/prior-traces/` | copied from the payload |
| `tests/golden/` | the merged-state test suite from the payload, verifier-only (the agent never sees it) |
| `tests/test.sh` | overlays `tests/golden/` onto `/app`, runs the suite, writes `/logs/verifier/reward.txt`; fails closed when golden tests are missing |
| `solution/solve.sh` | oracle placeholder (exits non-zero until implemented) |

The oracle and the test command are explicit TODO placeholders: a skeleton is
structure, not a solvable task, until a task author fills them in. Task names
are registry-safe and distinguish the archive (`peasant-archive-pr-0021`) from
the live repository (`peasant-pr-0021`).

Python API: `build_skeleton(payload_dir, dest, org=..., base_image=...)`.

## Harbor integration

`materialize` writes a directory that can be copied into a task's
`environment/` (or read directly from a verifier):

```text
pr.json
traces.jsonl
metadata.jsonl
bundle.json
transcripts/<session_id>.jsonl
```

Example task layout:

```text
tasks/my-trace-task/
├── task.toml
├── instruction.md
├── environment/
│   ├── Dockerfile            # COPY environment/corpus /workdir/corpus
│   └── corpus/pr-0343/       # output of `tracebench-corpus bundle ...`
├── solution/solve.sh
└── tests/
    ├── test.sh
    └── test_trace.py         # reads /workdir/corpus/bundle.json + transcripts
```

Generate the bundle before building the task:

```bash
tracebench-corpus --corpus corpus/dump bundle "peasant-labs/peasant#343" \
  --dest tasks/my-trace-task/environment/corpus/pr-0343
```

Inside a verifier, `load_corpus(path="/workdir/corpus")` also works: a
materialized bundle directory is a valid mini corpus (its `traces.jsonl` and
`metadata.jsonl` cover the PR's sessions).

The loader is stdlib-only; `huggingface_hub` is needed only for
`load_corpus(repo=...)`.
