"""Materialize a task's ``repo/`` as the project's real history up to ``tree_commit``.

The payload's ``repo-request.json`` names ``tree_commit`` (the pre-PR state).
``repo/`` ships every ancestor of that commit with its real SHAs and nothing at
or after the PR: HEAD is the real ``tree_commit`` SHA on the PR's base branch,
``git rev-list --all`` matches the source's count for that commit, and no later
commit (the PR's merge commit included) exists in the object store. The source
clone is only read (``pack-objects`` from it), never modified. Every check
fails closed, naming the commit.
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
AGENT_GIT_EMAIL = "agent@tracebench.local"


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


def _request(payload_dir: Path) -> dict:
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
    return request


def _tree_commit(repo_dir: Path, request: dict, request_path: Path) -> str:
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


def _base_branch(request: dict) -> str:
    branch = request.get("base_ref") or request.get("base_branch")
    if not isinstance(branch, str) or not branch.strip():
        return "main"
    return branch.removeprefix("refs/heads/").strip() or "main"


def _dest_git(dest: Path, commit: str, *args: str,
              check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(dest), *args], capture_output=True, text=True,
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
    request = _request(payload_dir)
    tree_commit = _tree_commit(repo_dir, request, payload_dir / "repo-request.json")
    try:
        commit_sha = _git(repo_dir, "rev-parse", "--verify", f"{tree_commit}^{{commit}}")
    except WorktreeError as exc:
        raise WorktreeError(
            f"materialize_worktree: tree_commit {tree_commit} is not a commit in {repo_dir}. "
            f"{exc}"
        ) from None
    expected_tree = _git(repo_dir, "rev-parse", f"{commit_sha}^{{tree}}")
    # Read-only on the source: the expected number of commits in the truncated repo.
    expected_count = _git(repo_dir, "rev-list", "--count", commit_sha)
    merge_commit = request.get("merge_commit") or None
    branch = _base_branch(request)

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
        pack = Path(tmp) / "history.pack"
        _build_truncated(repo_dir, out, pack, commit_sha, branch)
        _verify_truncated(out, commit_sha, expected_tree, expected_count, merge_commit, branch)
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


def _build_truncated(source: Path, out: Path, pack: Path, commit: str, branch: str) -> None:
    # Read-only against the source: pack exactly the ancestors of commit.
    proc = subprocess.run(
        ["git", "-C", str(source), "pack-objects", "--revs", "--stdout"],
        input=(commit + "\n").encode(), capture_output=True,
    )
    if proc.returncode != 0:
        raise WorktreeError(
            f"materialize_worktree: `git pack-objects` failed for tree_commit {commit} in "
            f"{source}: {proc.stderr.decode('utf-8', 'replace').strip()}. Check that the "
            "source clone is readable and the commit exists there."
        )
    pack.write_bytes(proc.stdout)

    out.mkdir()
    _dest_git(out, commit, "init", "-q")
    _dest_git(out, commit, "config", "core.logAllRefUpdates", "false")
    with pack.open("rb") as src:
        proc = subprocess.run(
            ["git", "-C", str(out), "index-pack", "--stdin"], stdin=src,
            capture_output=True, text=True,
        )
    if proc.returncode != 0:
        raise WorktreeError(
            f"materialize_worktree: `git index-pack` failed while building the truncated repo "
            f"for tree_commit {commit} in {out}: {proc.stderr.strip()}. The source pack is "
            "unreadable; re-run."
        )
    _dest_git(out, commit, "update-ref", f"refs/heads/{branch}", commit)
    _dest_git(out, commit, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    _dest_git(out, commit, "reset", "--hard", "-q")
    _dest_git(out, commit, "config", "core.logAllRefUpdates", "true")
    shutil.rmtree(out / ".git" / "logs", ignore_errors=True)
    for name in ("ORIG_HEAD", "FETCH_HEAD"):
        (out / ".git" / name).unlink(missing_ok=True)
    _dest_git(out, commit, "config", "user.name", AGENT_GIT_NAME)
    _dest_git(out, commit, "config", "user.email", AGENT_GIT_EMAIL)


def _verify_truncated(out: Path, commit: str, expected_tree: str, expected_count: str,
                      merge_commit: str | None, branch: str) -> None:
    def fail(check: str, detail: str) -> WorktreeError:
        return WorktreeError(
            f"materialize_worktree: truncated repo for tree_commit {commit} failed the "
            f"{check} check: {detail}. The agent could reach the wrong or later history; "
            "the task was not written. Re-fetch the source clone and re-run."
        )

    head = _dest_git(out, commit, "rev-parse", "HEAD").stdout.strip()
    if head != commit:
        raise fail("HEAD", f"HEAD is {head}, expected {commit}")
    ref = _dest_git(out, commit, "symbolic-ref", "HEAD").stdout.strip()
    if ref != f"refs/heads/{branch}":
        raise fail("branch", f"HEAD points at {ref}, expected refs/heads/{branch}")
    tree = _dest_git(out, commit, "rev-parse", "HEAD^{tree}").stdout.strip()
    if tree != expected_tree:
        raise fail("tree", f"HEAD tree is {tree}, source {commit}^{{tree}} is {expected_tree}")
    count = _dest_git(out, commit, "rev-list", "--all", "--count").stdout.strip()
    if count != expected_count:
        raise fail(
            "history-count",
            f"git rev-list --all --count is {count}, source git rev-list --count {commit} is "
            f"{expected_count}",
        )
    if int(count) <= 1:
        raise fail("history-count", f"git rev-list --all --count is {count}; expected real history")
    if (out / ".git" / "shallow").exists():
        raise fail("shallow", ".git/shallow exists; the repo must hold the full real history")
    remotes = _dest_git(out, commit, "remote").stdout.split()
    if remotes:
        raise fail("remotes", f"remotes {remotes} are configured")
    if merge_commit and _dest_git(out, commit, "cat-file", "-e", f"{merge_commit}^{{commit}}",
                                  check=False).returncode == 0:
        raise fail("fix-absent", f"merge commit {merge_commit} is present in the object store")
    for name in ("logs", "ORIG_HEAD", "FETCH_HEAD", "objects/info/alternates"):
        if (out / ".git" / name).exists():
            raise fail("no-metadata-leak", f".git/{name} exists")
    status = _dest_git(out, commit, "status", "--porcelain").stdout.strip()
    if status:
        raise fail("clean-worktree", f"git status is not clean: {status}")
    fsck = _dest_git(out, commit, "fsck", "--full", check=False)
    if fsck.returncode != 0 or fsck.stdout.strip() or fsck.stderr.strip():
        raise fail(
            "fsck",
            f"git fsck --full is not clean: {fsck.stdout.strip() or fsck.stderr.strip()}",
        )
    identity = _dest_git(out, commit, "config", "user.name").stdout.strip()
    email = _dest_git(out, commit, "config", "user.email").stdout.strip()
    if identity != AGENT_GIT_NAME or email != AGENT_GIT_EMAIL:
        raise fail("identity", f"local identity is {identity} <{email}>, expected "
                  f"{AGENT_GIT_NAME} <{AGENT_GIT_EMAIL}>")
