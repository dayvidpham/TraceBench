"""Snapshot assembly + deterministic serialization (C4: Serializer)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from snapshot.cutoff import Cutoff, resolve_cutoff_time
from snapshot.peasant import PeasantClient
from snapshot.repo import (
    Commit,
    FileEntry,
    list_history,
    list_tree,
    resolve_sha_before,
)
from snapshot.trace import (
    DirTraceProvider,
    StubTraceProvider,
    TraceFile,
    TraceProvider,
    materialize_traces,
)


@dataclass(frozen=True)
class Snapshot:
    cutoff_time: datetime
    cutoff_kind: str
    repo_sha: str
    files: tuple[FileEntry, ...]
    commits: tuple[Commit, ...]
    traces: tuple[TraceFile, ...]

    def to_dict(self) -> dict:
        def iso(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).isoformat(timespec="seconds")

        return {
            "cutoff_kind": self.cutoff_kind,
            "cutoff_time": iso(self.cutoff_time),
            "repo_sha": self.repo_sha,
            "commits": [
                {
                    "sha": c.sha,
                    "parents": list(c.parents),
                    "author_time": iso(c.author_time),
                    "committer_time": iso(c.committer_time),
                    "subject": c.subject,
                }
                for c in self.commits
            ],
            "files": [
                {
                    "path": f.path,
                    "blob_sha": f.blob_sha,
                    "mode": f.mode,
                    "size": f.size,
                }
                for f in self.files
            ],
            "traces": [
                {"path": t.path, "event_time": iso(t.event_time), "size": t.size}
                for t in self.traces
            ],
        }

    def manifest_hash(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def snapshot_repo(
    repo_path: str | Path,
    cutoff: Cutoff,
    trace_dir: str | Path | None = None,
    trace_provider: TraceProvider | None = None,
    peasant: PeasantClient | None = None,
    pr_start_override: str | datetime | None = None,
) -> Snapshot:
    """Build the snapshot for an arbitrary repo.

    Acceptance (issue #2):
    - date cutoff: no commit/file/trace event after the date.
    - PR cutoff: nothing at or after the PR start (exclusive).
    - deterministic: same call -> same snapshot (sorted + UTC).
    """
    repo = Path(repo_path)
    if not (repo / ".git").exists() and not (repo / "HEAD").exists():
        # bare repos have HEAD at top level; worktrees have .git file/dir.
        if not repo.exists():
            raise FileNotFoundError(f"repo not found: {repo}")

    cutoff_time = resolve_cutoff_time(cutoff, peasant, pr_start_override)
    exclusive = cutoff.kind == "pr"

    commits = list_history(repo, cutoff_time)
    if exclusive:
        commits = [c for c in commits if c.committer_time < cutoff_time]
    else:
        commits = [c for c in commits if c.committer_time <= cutoff_time]
    if not commits:
        # No commit satisfies the cutoff (e.g. PR starts before first commit).
        # Fall back to git so the error message names the cutoff.
        sha = resolve_sha_before(repo, cutoff_time)
        files: list[FileEntry] = []
    else:
        sha = max(commits, key=lambda c: (c.committer_time, c.sha)).sha
        files = list_tree(repo, sha)

    if trace_provider is None:
        trace_provider = (
            DirTraceProvider(Path(trace_dir)) if trace_dir else StubTraceProvider()
        )
    traces = tuple(trace_provider.list_files(cutoff_time))

    # Defensive: trace providers should already filter, but enforce the rule.
    # PR cutoffs are exclusive (nothing at/after PR start), dates inclusive.
    if exclusive:
        traces = tuple(t for t in traces if t.event_time < cutoff_time)
        commits_t = tuple(commits)
    else:
        traces = tuple(t for t in traces if t.event_time <= cutoff_time)
        commits_t = tuple(commits)

    return Snapshot(
        cutoff_time=cutoff_time,
        cutoff_kind=cutoff.kind,
        repo_sha=sha,
        files=tuple(files),
        commits=commits_t,
        traces=traces,
    )


def write_snapshot(snapshot: Snapshot, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = snapshot.to_dict()
    payload["manifest_sha256"] = snapshot.manifest_hash()
    (out / "history.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    return out / "history.json"


def materialize_snapshot(
    repo_path: str | Path,
    snapshot: Snapshot,
    out_dir: str | Path,
    trace_root: str | Path | None = None,
) -> Path:
    """Write history.json + repo/ tree (+ traces/ if a root is given)."""
    from snapshot.repo import materialize_tree as _mat_tree

    out = Path(out_dir)
    write_snapshot(snapshot, out)
    _mat_tree(Path(repo_path), snapshot.repo_sha, out / "repo")
    if trace_root is not None:
        materialize_traces(Path(trace_root), list(snapshot.traces), out / "traces")
    return out
