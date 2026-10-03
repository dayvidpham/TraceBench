# snapshot — repo / trace / history cutoff (issue #2)

```
full history -> cutoff (date or PR start) -> snapshot (repo tree, trace, git history)
```

Build-time package. Agent/verifier phases stay offline and read the
materialized output.

## Usage

```bash
# date cutoff (inclusive)
python3 -m snapshot.cli --repo /path/to/repo \
  --cutoff-type date --cutoff-date 2026-09-01T00:00:00Z --out /tmp/snap

# PR cutoff (exclusive: nothing at/after PR start, via peasant binary)
python3 -m snapshot.cli --repo /path/to/repo \
  --cutoff-type pr --pr 123 --peasant-bin /usr/local/bin/peasant --out /tmp/snap

# offline / tests: bypass the binary
python3 -m snapshot.cli --repo /path/to/repo \
  --cutoff-type pr --pr 123 --pr-start-override 2026-09-01T00:00:00Z \
  --out /tmp/snap --materialize
```

Output: `history.json` (+ `manifest_sha256`), and with `--materialize`
also `repo/` (exact `git archive` tree) and `traces/`.

## Peasant binary contract

Default: `peasant pr show <n> --format json`, stdout JSON with one of
`started_at | created_at | createdAt | start_time | startedAt`.
Override with `--peasant-argv "pr|show|{pr}|--format|json"`.
Pin with `--expected-peasant-version <sha>` (checked via `peasant --version`).

## Trace (stub for now)

`trace/` is a bunch of files the agent sees during execution. Today:
`StubTraceProvider` (-> `[]`) by default, `DirTraceProvider(trace_dir)`
filters by file mtime `<= cutoff` (date) / `< pr_start` (PR).
Swap in the real store behind the `TraceProvider` protocol later.

## Layout

- `cutoff.py` — CutoffResolver
- `peasant.py` — BinaryPeasantClient / StubPeasantClient
- `repo.py` — RepoSnapshotter (`ls-tree`) + HistoryExtractor (`log --before`)
- `trace.py` — TraceCollector
- `api.py` — assemble + deterministic serialize (sorted, UTC)
