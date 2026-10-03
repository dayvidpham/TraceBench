"""Git repo access (C4: RepoSnapshotter + HistoryExtractor)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileEntry:
    path: str
    blob_sha: str
    mode: str
    size: int


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    author_time: datetime
    committer_time: datetime
    subject: str


def _git(repo: Path, *args: str, timeout_sec: float = 60.0) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git timed out: {' '.join(args)}") from exc
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:2000]}"
        )
    return proc.stdout


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def resolve_sha_before(repo: Path, cutoff: datetime) -> str:
    """Newest commit at or before cutoff (committer date)."""
    out = _git(repo, "rev-list", "-1", f"--before={_fmt_time(cutoff)}", "HEAD").strip()
    if not out:
        raise GitError(f"no commit at or before cutoff {_fmt_time(cutoff)}")
    return out


def _blob_size(repo: Path, blob_sha: str) -> int:
    out = _git(repo, "cat-file", "-s", blob_sha).strip()
    return int(out)


def list_tree(repo: Path, sha: str) -> list[FileEntry]:
    """Deterministic (path-sorted) blob list at sha. Mirrors the tree-vs-blob
    check in scripts/verify-issue-1.sh: working tree must equal HEAD blobs."""
    out = _git(repo, "ls-tree", "-r", "--full-tree", sha)
    entries: list[FileEntry] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        # format: "<mode> <type> <sha>\t<path>"
        meta, path = line.split("\t", 1)
        mode, obj_type, obj_sha = meta.split()
        if obj_type != "blob":
            continue
        entries.append(
            FileEntry(
                path=path,
                blob_sha=obj_sha,
                mode=mode,
                size=_blob_size(repo, obj_sha),
            )
        )
    entries.sort(key=lambda e: e.path)
    return entries


def _parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def list_history(repo: Path, cutoff: datetime) -> list[Commit]:
    """Commits with committer date <= cutoff, oldest-first, SHA tie-break."""
    fmt = "%H%x00%P%x00%aI%x00%cI%x00%s%x1f"
    out = _git(repo, "log", f"--before={_fmt_time(cutoff)}", f"--format={fmt}")
    commits: list[Commit] = []
    for rec in out.split("\x1f"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, parents, author, committer, subject = rec.split("\x00")
        commits.append(
            Commit(
                sha=sha,
                parents=tuple(parents.split()) if parents.strip() else (),
                author_time=_parse_time(author),
                committer_time=_parse_time(committer),
                subject=subject,
            )
        )
    commits.sort(key=lambda c: (c.committer_time, c.sha))
    return commits


def materialize_tree(repo: Path, sha: str, dest: Path) -> None:
    """Write the exact tree at sha into dest/repo via git archive (no checkout)."""
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", sha],
        capture_output=True,
        timeout=120.0,
    )
    if archive.returncode != 0:
        raise GitError(f"git archive {sha} failed: {archive.stderr[:1000]!r}")
    extract = subprocess.run(
        ["tar", "-x", "-C", str(dest)],
        input=archive.stdout,
        capture_output=True,
        timeout=120.0,
    )
    if extract.returncode != 0:
        raise GitError(f"tar extract failed: {extract.stderr[:1000]!r}")
