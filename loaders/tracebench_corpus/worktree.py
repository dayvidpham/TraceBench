"""Materialize a task's secure ``repo/`` worktree through the Go snapshot tool.

The payload's ``repo-request.json`` names ``tree_commit`` (the pre-PR state).
The snapshot CLI runs in commit mode, which pins the exact tree of that commit
and writes no ``.git``. The selected tree is checked against
``git rev-parse <tree_commit>^{tree}`` and the call fails closed on drift.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SNAPSHOT_MODULE = Path(__file__).resolve().parents[2] / "snapshot"


class WorktreeError(ValueError):
    """The secure worktree could not be materialized or failed verification."""


@dataclass(frozen=True)
class WorktreeResult:
    repo_path: Path
    tree_commit: str
    tree_sha: str


def _git(repo_dir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise WorktreeError(
            f"materialize_worktree: `git {' '.join(args)}` failed in {repo_dir}: "
            f"{proc.stderr.strip()}. Check that the commit exists in this clone "
            "(git fetch the PR refs) and that repo_dir is the source repository."
        )
    return proc.stdout.strip()


def _tree_commit(repo_dir: Path, payload_dir: Path) -> str:
    request_path = payload_dir / "repo-request.json"
    try:
        request = json.loads(request_path.read_text())
    except FileNotFoundError:
        raise WorktreeError(
            f"materialize_worktree: {request_path} is missing; build the task payload first."
        ) from None
    except json.JSONDecodeError as exc:
        raise WorktreeError(
            f"materialize_worktree: {request_path} is not valid JSON ({exc}); rebuild the payload."
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise WorktreeError(
            f"materialize_worktree: cannot read {request_path} ({exc}); check that it is a "
            "readable UTF-8 file and rebuild the payload."
        ) from None
    if not isinstance(request, dict):
        raise WorktreeError(
            f"materialize_worktree: {request_path} must hold a JSON object, got "
            f"{type(request).__name__}; rebuild the payload."
        )
    tree_commit = request.get("tree_commit")
    if tree_commit:
        return tree_commit
    merge_commit = request.get("merge_commit")
    if not merge_commit:
        raise WorktreeError(
            f"materialize_worktree: {request_path} has neither tree_commit nor merge_commit; "
            "rebuild the payload with --index so the PR boundary is known."
        )
    return _git(repo_dir, "rev-parse", "--verify", f"{merge_commit}^")


def _snapshot_argv(snapshot_bin: str | Path | None) -> tuple[list[str], Path | None]:
    if snapshot_bin is not None:
        return [str(snapshot_bin)], None
    return ["go", "run", "./cmd/snapshot"], SNAPSHOT_MODULE


def materialize_worktree(
    repo_dir: str | Path,
    payload_dir: str | Path,
    snapshot_bin: str | Path | None = None,
) -> WorktreeResult:
    """Write ``payload_dir/repo`` at the payload's ``tree_commit`` and verify it."""
    repo_dir = Path(repo_dir).resolve()
    payload_dir = Path(payload_dir).resolve()
    tree_commit = _tree_commit(repo_dir, payload_dir)
    # Fail closed, naming the commit, before invoking the snapshot tool.
    try:
        commit_sha = _git(repo_dir, "rev-parse", "--verify", f"{tree_commit}^{{commit}}")
    except WorktreeError as exc:
        raise WorktreeError(
            f"materialize_worktree: tree_commit {tree_commit} is not a commit in {repo_dir}. "
            f"{exc}"
        ) from None
    expected_tree = _git(repo_dir, "rev-parse", f"{commit_sha}^{{tree}}")

    dest = payload_dir / "repo"
    try:
        occupied = dest.exists() and any(dest.iterdir())
    except OSError as exc:
        raise WorktreeError(
            f"materialize_worktree: cannot use {dest} as the worktree destination for "
            f"tree_commit {commit_sha} ({exc}); remove it so a directory can be written."
        ) from None
    if occupied:
        raise WorktreeError(
            f"materialize_worktree: {dest} is not empty; remove it before re-materializing."
        )

    argv, cwd = _snapshot_argv(snapshot_bin)
    with tempfile.TemporaryDirectory(prefix="tracebench-worktree-") as tmp:
        out = Path(tmp) / "out"
        cmd = [
            *argv, "--repo", str(repo_dir), "--cutoff-type", "commit",
            "--commit", commit_sha, "--out", str(out), "--materialize",
        ]
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        except OSError as exc:
            raise WorktreeError(
                f"materialize_worktree: cannot run the snapshot tool ({exc}); install Go or "
                "pass snapshot_bin pointing at a built, executable `snapshot` binary."
            ) from None
        if proc.returncode != 0:
            raise WorktreeError(
                f"materialize_worktree: snapshot failed for tree_commit {commit_sha}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        history = _read_history(out / "history.json", commit_sha)
        got_tree = history.get("tree_sha")
        if got_tree != expected_tree:
            raise WorktreeError(
                f"materialize_worktree: tree drift for tree_commit {commit_sha}: snapshot "
                f"selected tree {got_tree!r} but git rev-parse {commit_sha}^{{tree}} is "
                f"{expected_tree}. The snapshot tool must run in commit mode; rebuild it."
            )
        materialized = out / "repo"
        _verify_tree(materialized, commit_sha)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            try:
                dest.rmdir()
            except OSError as exc:
                raise WorktreeError(
                    f"materialize_worktree: cannot replace the empty destination {dest} for "
                    f"tree_commit {commit_sha} ({exc}); remove it (it may be a symlink) and "
                    "re-run."
                ) from None
        shutil.move(str(materialized), str(dest))

    return WorktreeResult(
        repo_path=dest, tree_commit=commit_sha, tree_sha=expected_tree
    )


def _read_history(path: Path, commit: str) -> dict:
    try:
        history = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorktreeError(
            f"materialize_worktree: snapshot for tree_commit {commit} produced an unreadable "
            f"{path.name} ({exc}); the snapshot tool must write valid JSON; rebuild it."
        ) from None
    if not isinstance(history, dict):
        raise WorktreeError(
            f"materialize_worktree: snapshot for tree_commit {commit} wrote {path.name} as "
            f"{type(history).__name__}, not a JSON object; rebuild the snapshot tool."
        )
    return history


def _verify_tree(repo: Path, commit: str) -> None:
    if not repo.is_dir() or not any(repo.iterdir()):
        raise WorktreeError(
            f"materialize_worktree: materialized repo/ for tree_commit {commit} is empty; "
            "the commit's tree has no files or the snapshot tool wrote nothing."
        )
    leaked = [p for p in repo.rglob(".git") if p.exists()]
    if leaked:
        raise WorktreeError(
            f"materialize_worktree: materialized repo/ for tree_commit {commit} contains "
            f"git metadata ({', '.join(str(p.relative_to(repo)) for p in leaked)}); the "
            "agent must not reach later commits. Remove it from the source tree."
        )
