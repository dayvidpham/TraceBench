"""Batched reads from the git object database.

Task assembly reads many files at one commit: every test file for the golden
suite and every Go suite for the case catalog. ``read_blobs`` serves them with
one ``git cat-file --batch`` process instead of one ``git show`` per file and
fails closed on a missing object or a malformed stream.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: Git executable.
GIT_BINARY = "git"


def read_blobs(repo_dir: str | Path, commit: str, paths: list[str]) -> dict[str, bytes]:
    """Read ``commit:path`` for every path in one git process.

    Returns the content keyed by path. Raises ValueError when git cannot run,
    the commit or a path is missing, or the stream is malformed; an empty
    ``paths`` returns an empty mapping without running git.
    """
    if not paths:
        return {}
    request = b"".join(
        os.fsencode(commit) + b":" + os.fsencode(path) + b"\n" for path in paths
    )
    try:
        result = subprocess.run(
            [GIT_BINARY, "-C", str(repo_dir), "cat-file", "--batch"],
            input=request,
            capture_output=True,
        )
    except OSError as exc:
        raise ValueError(f"cannot run {GIT_BINARY}: {exc}; install git") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError(
            f"git cat-file --batch in {repo_dir} at {commit} failed: "
            f"{detail or 'no error output'}; check that --repo-dir is a clone containing "
            "the commit"
        )
    return _parse_batch(result.stdout, repo_dir, commit, paths)


def _parse_batch(
    data: bytes, repo_dir: str | Path, commit: str, paths: list[str]
) -> dict[str, bytes]:
    """Split a ``git cat-file --batch`` stream into one content per request."""
    blobs: dict[str, bytes] = {}
    position = 0
    for path in paths:
        end = data.find(b"\n", position)
        if end < 0:
            raise ValueError(_malformed(repo_dir, commit, path, "the stream ended early"))
        header = data[position:end]
        position = end + 1
        parts = header.split(b" ")
        if len(parts) == 2 and parts[1] in (b"missing", b"ambiguous"):
            raise ValueError(
                _malformed(repo_dir, commit, path, f"the object is {parts[1].decode('ascii')}")
            )
        if len(parts) != 3 or parts[1] != b"blob":
            raise ValueError(
                _malformed(repo_dir, commit, path, "the header is not a blob record")
            )
        try:
            size = int(parts[2])
        except ValueError:
            raise ValueError(
                _malformed(repo_dir, commit, path, "the size is not a number")
            ) from None
        content = data[position : position + size]
        if len(content) != size or data[position + size : position + size + 1] != b"\n":
            raise ValueError(_malformed(repo_dir, commit, path, "the content is truncated"))
        blobs[path] = content
        position += size + 1
    return blobs


def _malformed(repo_dir: str | Path, commit: str, path: str, detail: str) -> str:
    return (
        f"git cat-file: {commit}:{path} in {repo_dir}: {detail}; "
        "check that --repo-dir is a clone containing the commit"
    )
