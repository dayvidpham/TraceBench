"""Materialize the golden test suite from a pull request's merged state.

Every file at ``merge_commit`` whose repository-relative path matches one of
the test patterns (doublestar globs) is read from the git object database,
never the working tree, and written into the payload's ``tests/`` directory
at the same relative path. ``tests/manifest.json`` records the commit, the
patterns, and the sorted extracted paths.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable

MANIFEST_NAME = "manifest.json"

_GIT_BINARY = "git"
_SYMLINK_MODE = "120000"
_BLOB = "blob"
_EXECUTABLE_MODE = "100755"


class GoldenSuiteError(ValueError):
    """The golden suite could not be materialized."""


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a doublestar glob, relative to the repository root.

    ``*`` and ``?`` never cross ``/``; a ``**`` that is not a whole path
    segment behaves like ``*``; a leading ``**/`` matches zero or more leading
    directories; a trailing ``/**`` matches everything beneath a directory;
    ``[...]`` is a character class.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        char = pattern[i]
        if pattern.startswith("**", i):
            at_segment_start = i == 0 or pattern[i - 1] == "/"
            if at_segment_start and pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if at_segment_start and i + 2 == n:
                out.append(".*")
                i += 2
                continue
            # A ``**`` inside a segment is an ordinary ``*``: it never crosses ``/``.
            out.append("[^/]*")
            i += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 2 if pattern.startswith("[!", i) or pattern.startswith("[^", i) else i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = pattern[i + 1:end]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = end + 1
                continue
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("".join(out) + r"\Z", re.DOTALL)


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    """Whether a repository-relative path matches any doublestar pattern."""
    return any(glob_to_regex(pattern).match(path) for pattern in patterns)


def materialize_golden_tests(
    repo_dir: str | Path,
    merge_commit: str,
    patterns: Iterable[str],
    dest: str | Path,
    *,
    pr_id: str = "",
) -> list[str]:
    """Extract matching test files at ``merge_commit`` into ``dest``.

    Returns the sorted extracted paths and writes ``dest/manifest.json``.
    Raises :class:`GoldenSuiteError` when nothing matches, naming the pull
    request and the patterns, so no runnable-looking task without acceptance
    criteria is emitted.
    """
    patterns = list(patterns)
    dest = Path(dest)
    compiled = [glob_to_regex(pattern) for pattern in patterns]
    selected: list[str] = []
    modes: dict[str, str] = {}
    for mode, kind, path in _ls_tree(repo_dir, merge_commit):
        if kind != _BLOB or mode == _SYMLINK_MODE:
            continue
        if any(regex.match(path) for regex in compiled):
            if path == MANIFEST_NAME:
                raise GoldenSuiteError(
                    f"golden suite for pull request {pr_id or '<unknown>'}: the test file "
                    f"{path!r} at merge commit {merge_commit} collides with the golden "
                    f"suite manifest {dest / MANIFEST_NAME}. Narrow the test patterns in "
                    "repo-request.json so they exclude the root-level manifest.json."
                )
            selected.append(path)
            modes[path] = mode
    selected.sort()
    if not selected:
        raise GoldenSuiteError(
            f"golden suite for pull request {pr_id or '<unknown>'} is empty: no file at "
            f"merge commit {merge_commit} in {repo_dir} matches the test patterns "
            f"{patterns}. The task would have no acceptance criteria, so it was not built. "
            "Check that the merge commit is correct and that repo-request.json "
            "test_patterns cover the repository's test files."
        )
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    for path in selected:
        target = (dest / PurePosixPath(path)).resolve()
        if root not in target.parents:
            raise GoldenSuiteError(
                f"refusing to write test file {path!r} from {merge_commit}: it resolves "
                f"outside the golden suite directory {dest}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_git_bytes(repo_dir, "show", f"{merge_commit}:{path}"))
        if modes[path] == _EXECUTABLE_MODE:
            target.chmod(0o755)
    (dest / MANIFEST_NAME).write_text(
        json.dumps(
            {"commit": merge_commit, "patterns": patterns, "paths": selected}, indent=2
        )
        + "\n"
    )
    return selected


def _ls_tree(repo_dir: str | Path, commit: str) -> list[tuple[str, str, str]]:
    raw = _git_bytes(repo_dir, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries: list[tuple[str, str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, kind, _sha = meta.decode("ascii").split(" ")
        entries.append((mode, kind, name.decode("utf-8", "surrogateescape")))
    return entries


def _git_bytes(repo_dir: str | Path, *args: str) -> bytes:
    command = [_GIT_BINARY, "-C", str(repo_dir), *args]
    try:
        result = subprocess.run(command, capture_output=True)
    except OSError as exc:
        raise GoldenSuiteError(f"cannot run {_GIT_BINARY}: {exc}; install git") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise GoldenSuiteError(
            f"git {' '.join(args)} in {repo_dir} failed while materializing the golden "
            f"suite: {message}. Check that --repo-dir is a clone containing the commit."
        )
    return result.stdout
