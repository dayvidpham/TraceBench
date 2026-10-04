"""Generate a task oracle from a pull request's merge diff.

The oracle is ``git diff <tree_commit> <merge_commit>``: applied to the pre-PR
tree it must reproduce ``merge_commit^{tree}`` exactly. ``build_oracle``
verifies that equivalence in a temporary detached worktree and fails closed on
any mismatch, so a task is never emitted with an oracle that does not solve it.

The patch and ``solve.sh`` are oracle-only: they are written under the task's
``solution/`` directory, which Harbor copies into the container for oracle
runs only.
"""

from __future__ import annotations

import json
import shlex
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Payload-relative location of the oracle patch.
PATCH_PATH = "solution/oracle.patch"
#: Payload-relative location of the oracle script.
SOLVE_PATH = "solution/solve.sh"


@dataclass(frozen=True)
class Oracle:
    """A verified oracle for one pull request."""

    patch: bytes
    solve_sh: str
    tree_commit: str
    merge_commit: str
    changed_files: tuple[str, ...]
    applied_tree: str
    verified: bool

    def task_block(self) -> dict[str, Any]:
        """The ``oracle`` block recorded in ``task.json``."""
        return {
            "patch": PATCH_PATH,
            "tree_commit": self.tree_commit,
            "merge_commit": self.merge_commit,
            "changed_files": len(self.changed_files),
            "applied_tree": self.applied_tree,
            "verified": self.verified,
        }


def _git(repo_dir: Path, *args: str, input: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args], input=input, capture_output=True
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise ValueError(
            f"oracle: `git {' '.join(args)}` failed in {repo_dir} "
            f"(exit {result.returncode}): {stderr or 'no error output'}; "
            "check that --repo-dir is a clone containing both commits"
        )
    return result.stdout


def _rev(repo_dir: Path, ref: str) -> str:
    return _git(repo_dir, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()


def solve_script(build_command: str | None) -> str:
    """The ``solve.sh`` body: apply the patch and run the build command."""
    build = build_command.strip() if build_command else ""
    lines = [
        "#!/bin/bash",
        "# Oracle solution: apply the pull request's merge diff, then build.",
        "set -euo pipefail",
        'SOLUTION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'APP_DIR="${APP_DIR:-/workdir/repo}"',
        'PATCH="$SOLUTION_DIR/oracle.patch"',
        'cd "$APP_DIR"',
        'if ! git apply --check "$PATCH"; then',
        '  echo "tracebench: oracle.patch does not apply to $APP_DIR" >&2',
        "  exit 1",
        "fi",
        'git apply "$PATCH"',
    ]
    if build:
        lines += [
            f"# Build command: {build}",
            f"if ! bash -c {shlex.quote(build)}; then",
            '  echo "tracebench: oracle build command failed" >&2',
            "  exit 1",
            "fi",
        ]
    lines.append("")
    return "\n".join(lines)


def build_oracle(
    repo_dir: str | Path,
    tree_commit: str,
    merge_commit: str,
    build_command: str | None,
) -> Oracle:
    """Build and verify the oracle for ``tree_commit..merge_commit``.

    Raises ``ValueError`` when git fails, the patch does not apply, or the
    applied tree differs from ``merge_commit^{tree}``.
    """
    repo = Path(repo_dir)
    tree_sha = _rev(repo, tree_commit)
    merge_sha = _rev(repo, merge_commit)
    diff_args = ("--binary", "--full-index", "--no-color", "--no-ext-diff", tree_sha, merge_sha)
    patch = _git(repo, "diff", *diff_args)
    names = _git(repo, "diff", "--name-only", "-z", "--no-renames", tree_sha, merge_sha)
    changed = tuple(sorted(name for name in names.decode().split("\0") if name))
    expected_tree = _git(repo, "rev-parse", f"{merge_sha}^{{tree}}").decode().strip()
    applied_tree = _verify(repo, tree_sha, merge_sha, patch, expected_tree)
    return Oracle(
        patch=patch,
        solve_sh=solve_script(build_command),
        tree_commit=tree_sha,
        merge_commit=merge_sha,
        changed_files=changed,
        applied_tree=applied_tree,
        verified=True,
    )


def _verify(repo: Path, tree_sha: str, merge_sha: str, patch: bytes, expected_tree: str) -> str:
    with tempfile.TemporaryDirectory(prefix="tracebench-oracle-") as scratch:
        worktree = Path(scratch) / "tree"
        _git(repo, "worktree", "add", "--detach", "-q", str(worktree), tree_sha)
        try:
            if patch:
                _git(worktree, "apply", "--check", "--index", input=patch)
                _git(worktree, "apply", "--index", input=patch)
            applied = _git(worktree, "write-tree").decode().strip()
        finally:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                capture_output=True,
            )
    if applied != expected_tree:
        raise ValueError(
            f"oracle: equivalence check failed: applying the patch to tree_commit {tree_sha} "
            f"yields tree {applied}, but merge_commit {merge_sha} has tree {expected_tree}; "
            "no oracle was written. Check that tree_commit is the merge's pre-PR parent."
        )
    return applied


def write_oracle(payload_dir: str | Path, oracle: Oracle) -> Path:
    """Write ``solution/`` into a payload and record the ``task.json`` oracle block."""
    payload = Path(payload_dir)
    task_path = payload / "task.json"
    summary: dict[str, Any] = {}
    if task_path.is_file():
        summary = json.loads(task_path.read_text())
    solution = payload / "solution"
    solution.mkdir(parents=True, exist_ok=True)
    (payload / PATCH_PATH).write_bytes(oracle.patch)
    solve = payload / SOLVE_PATH
    solve.write_text(oracle.solve_sh)
    solve.chmod(solve.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    summary["oracle"] = oracle.task_block()
    task_path.write_text(json.dumps(summary, indent=2) + "\n")
    return solution


def payload_commits(payload_dir: str | Path, repo_dir: str | Path) -> tuple[str, str]:
    """``(tree_commit, merge_commit)`` from a payload's ``repo-request.json``.

    A null ``tree_commit`` falls back to ``merge_commit^``.
    """
    request_path = Path(payload_dir) / "repo-request.json"
    if not request_path.is_file():
        raise ValueError(
            f"oracle: missing {request_path}; run the `task` subcommand to assemble the payload first"
        )
    try:
        request = json.loads(request_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"oracle: {request_path} is not valid JSON: {exc}") from exc
    merge_commit = request.get("merge_commit")
    if not merge_commit:
        raise ValueError(
            f"oracle: {request_path} has no merge_commit; rebuild the payload with --index"
        )
    tree_commit = request.get("tree_commit") or _rev(Path(repo_dir), f"{merge_commit}^")
    return tree_commit, merge_commit
