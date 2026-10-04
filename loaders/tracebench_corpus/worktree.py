"""Materialize a task's ``repo/`` as a truncated single-commit git repository.

The payload's ``repo-request.json`` names ``tree_commit`` (the pre-PR state).
``repo/`` becomes a shallow clone holding exactly that commit: HEAD is the real
``tree_commit`` SHA on ``main``, ``git rev-list --all`` has one entry, there
are no remotes, and no later commit (the PR's merge commit included) is
reachable or present. The source clone is only read (``fetch`` from it), never
modified. Every check fails closed, naming the commit.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# The Go snapshot module (still built by the pipeline CLI tests).
SNAPSHOT_MODULE = Path(__file__).resolve().parents[2] / "snapshot"

AGENT_GIT_NAME = "TraceBench Agent"
AGENT_GIT_EMAIL = "agent@tracebench.invalid"


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


def _dest_git(dest: Path, commit: str, *args: str, env: dict | None = None,
              check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(dest), *args], capture_output=True, text=True,
        env={**os.environ, **(env or {})},
    )
    if check and proc.returncode != 0:
        raise WorktreeError(
            f"materialize_worktree: `git {' '.join(args)}` failed while building the "
            f"truncated repo for tree_commit {commit} in {dest}: {proc.stderr.strip()}. "
            "Check that git is installed and the source clone is readable."
        )
    return proc


def materialize_worktree(
    repo_dir: str | Path,
    payload_dir: str | Path,
    snapshot_bin: str | Path | None = None,
) -> WorktreeResult:
    """Write ``payload_dir/repo`` as a one-commit repo at ``tree_commit`` and verify it.

    ``snapshot_bin`` is accepted for call-site compatibility and is unused: the
    repository is built with git directly.
    """
    del snapshot_bin
    repo_dir = Path(repo_dir).resolve()
    payload_dir = Path(payload_dir).resolve()
    tree_commit = _tree_commit(repo_dir, payload_dir)
    try:
        commit_sha = _git(repo_dir, "rev-parse", "--verify", f"{tree_commit}^{{commit}}")
    except WorktreeError as exc:
        raise WorktreeError(
            f"materialize_worktree: tree_commit {tree_commit} is not a commit in {repo_dir}. "
            f"{exc}"
        ) from None
    expected_tree = _git(repo_dir, "rev-parse", f"{commit_sha}^{{tree}}")
    merge_commit = _merge_commit(payload_dir)

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

    with tempfile.TemporaryDirectory(prefix="tracebench-worktree-") as tmp:
        out = Path(tmp) / "repo"
        _build_truncated(repo_dir, out, commit_sha)
        _verify_truncated(out, commit_sha, expected_tree, merge_commit)
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
        shutil.move(str(out), str(dest))

    return WorktreeResult(repo_path=dest, tree_commit=commit_sha, tree_sha=expected_tree)


def _merge_commit(payload_dir: Path) -> str | None:
    request = json.loads((payload_dir / "repo-request.json").read_text())
    merge = request.get("merge_commit")
    return merge if isinstance(merge, str) and merge else None


def _build_truncated(source: Path, out: Path, commit: str) -> None:
    out.mkdir()
    _dest_git(out, commit, "init", "-q")
    # Read-only against the source: upload-pack serves the one commit, depth 1.
    _dest_git(
        out, commit, "-c", "protocol.file.allow=always", "fetch", "-q", "--depth=1",
        "--no-tags", str(source), commit,
        env={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "uploadpack.allowAnySHA1InWant",
             "GIT_CONFIG_VALUE_0": "true"},
    )
    _dest_git(out, commit, "update-ref", "refs/heads/main", "FETCH_HEAD")
    _dest_git(out, commit, "symbolic-ref", "HEAD", "refs/heads/main")
    _dest_git(out, commit, "reset", "--hard", "-q")
    for name in ("FETCH_HEAD", "ORIG_HEAD"):
        (out / ".git" / name).unlink(missing_ok=True)
    _dest_git(out, commit, "config", "user.name", AGENT_GIT_NAME)
    _dest_git(out, commit, "config", "user.email", AGENT_GIT_EMAIL)


def _verify_truncated(out: Path, commit: str, expected_tree: str,
                      merge_commit: str | None) -> None:
    def fail(check: str, detail: str) -> WorktreeError:
        return WorktreeError(
            f"materialize_worktree: truncated repo for tree_commit {commit} failed the "
            f"{check} check: {detail}. The agent could reach the wrong or later history; "
            "the task was not written. Re-fetch the source clone and re-run."
        )

    head = _dest_git(out, commit, "rev-parse", "HEAD").stdout.strip()
    if head != commit:
        raise fail("HEAD", f"HEAD is {head}, expected {commit}")
    tree = _dest_git(out, commit, "rev-parse", "HEAD^{tree}").stdout.strip()
    if tree != expected_tree:
        raise fail("tree", f"HEAD tree is {tree}, source {commit}^{{tree}} is {expected_tree}")
    count = _dest_git(out, commit, "rev-list", "--all", "--count").stdout.strip()
    if count != "1":
        raise fail("single-commit", f"git rev-list --all --count is {count}, expected 1")
    shallow = out / ".git" / "shallow"
    lines = shallow.read_text().split() if shallow.is_file() else []
    if lines != [commit]:
        raise fail("shallow", f".git/shallow holds {lines}, expected [{commit}]")
    remotes = _dest_git(out, commit, "remote").stdout.split()
    if remotes:
        raise fail("remotes", f"remotes {remotes} are configured")
    if merge_commit and _dest_git(out, commit, "cat-file", "-e", f"{merge_commit}^{{commit}}",
                                  check=False).returncode == 0:
        raise fail("fix-absent", f"merge commit {merge_commit} is present")
    status = _dest_git(out, commit, "status", "--porcelain").stdout.strip()
    if status:
        raise fail("clean-worktree", f"git status is not clean: {status}")
