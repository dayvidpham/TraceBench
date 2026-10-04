"""Batched git object reads."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tracebench_corpus.blobs import read_blobs


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def _init(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").decode().strip()


def test_read_blobs_matches_the_per_file_read(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "plain.go").write_bytes(b"package pkg\n")
    (repo / "empty.go").write_bytes(b"")
    (repo / "binary.go").write_bytes(b"\x00\x01\xff\n\x00tail")
    commit = _commit(repo, "base")

    paths = ["binary.go", "empty.go", "pkg/plain.go"]
    blobs = read_blobs(repo, commit, paths)
    assert blobs == {
        "binary.go": b"\x00\x01\xff\n\x00tail",
        "empty.go": b"",
        "pkg/plain.go": b"package pkg\n",
    }
    for path in paths:
        expected = _git(repo, "show", f"{commit}:{path}")
        assert blobs[path] == expected


def test_read_blobs_empty_paths_returns_empty(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    assert read_blobs(repo, "HEAD", []) == {}


def test_read_blobs_missing_path_fails_naming_it(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "present.go").write_bytes(b"package p\n")
    commit = _commit(repo, "base")
    with pytest.raises(ValueError, match=r"missing\.go.*missing"):
        read_blobs(repo, commit, ["missing.go"])


def test_read_blobs_unknown_commit_names_the_repo_dir_fix(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "present.go").write_bytes(b"package p\n")
    _commit(repo, "base")
    with pytest.raises(ValueError, match="--repo-dir"):
        read_blobs(repo, "0" * 40, ["present.go"])
