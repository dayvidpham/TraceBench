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
