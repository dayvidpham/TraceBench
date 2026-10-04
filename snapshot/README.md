# snapshot — repo / trace / history cutoff (issue #2)

```
full history -> cutoff (date or PR start) -> snapshot (repo tree, trace, git history)
```

Build-time Go module. Agent/verifier phases stay offline and read the
materialized output. Go fits because sessions/traces are peasant's domain:
large JSONL transcripts, a unified schema, and SQLite-backed ingest are
already Go — this package shares the toolchain, static typing, and the
hermetic `go build`/`go test` flow proven by `peasant-smoke`.

## Usage

```bash
cd snapshot

# date cutoff (inclusive)
go run ./cmd/snapshot --repo /path/to/repo \
  --cutoff-type date --cutoff-date 2026-09-01T00:00:00Z --out /tmp/snap

# PR cutoff (exclusive: nothing at/after PR start, via peasant binary)
go run ./cmd/snapshot --repo /path/to/repo \
  --cutoff-type pr --pr 123 --peasant-bin /usr/local/bin/peasant --out /tmp/snap

# offline / tests: bypass the binary
go run ./cmd/snapshot --repo /path/to/repo \
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
Once peasant exposes a stable Go API for session/commit/PR mapping, the
`PeasantClient` interface can gain a native implementation next to
`BinaryPeasantClient` without changing callers.

## Trace (stub for now)

`trace/` is a bunch of files the agent sees during execution. Today:
`StubTraceProvider` (-> `[]`) by default, `DirTraceProvider{Root}`
filters by file mtime `<= cutoff` (date) / `< pr_start` (PR).
Swap in the real store behind the `TraceProvider` interface later —
streaming JSONL decode and concurrent walks are where Go pays off.

## Layout

- `cutoff.go` — CutoffResolver
- `peasant.go` — BinaryPeasantClient / StubPeasantClient
- `repo.go` — RepoSnapshotter (`ls-tree`) + HistoryExtractor (`log --before`)
- `trace.go` — TraceCollector
- `api.go` — assemble + deterministic serialize (sorted, UTC)
- `cmd/snapshot` — CLI
