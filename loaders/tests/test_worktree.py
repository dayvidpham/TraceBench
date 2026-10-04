"""Secure worktree materialization through the Go snapshot tool."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tracebench_corpus.worktree import SNAPSHOT_MODULE, WorktreeError, materialize_worktree

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="needs the Go toolchain")


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, name: str, content: str, date: str) -> str:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name, date=date)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture(scope="session")
def snapshot_bin(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("bin") / "snapshot"
    subprocess.run(["go", "build", "-o", str(out), "./cmd/snapshot"],
                   cwd=SNAPSHOT_MODULE, check=True)
    return out


@pytest.fixture
def repo(tmp_path) -> dict:
    path = tmp_path / "src"
    path.mkdir()
    _git(path, "init", "-q")
    pre = _commit(path, "a.txt", "pre", "2026-01-01T00:00:00+00:00")
    merge = _commit(path, "pr.txt", "pr", "2026-02-01T00:00:00+00:00")
    return {"path": path, "pre": pre, "merge": merge}


def _payload(tmp_path: Path, **request) -> Path:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "repo-request.json").write_text(json.dumps(request))
    return payload


def _wrapper(tmp_path: Path, real: Path, post: str) -> Path:
    """A snapshot binary that runs the real one, then tampers with its output."""
    script = tmp_path / "fake-snapshot"
    script.write_text(
        "#!/bin/sh\nset -e\n"
        f'"{real}" "$@"\n'
        'while [ $# -gt 0 ]; do [ "$1" = --out ] && OUT="$2"; shift; done\n'
        f"{post}\n"
    )
    script.chmod(0o755)
    return script


def test_exact_tree_at_tree_commit(tmp_path, repo, snapshot_bin):
    payload = _payload(tmp_path, tree_commit=repo["pre"], merge_commit=repo["merge"])
    result = materialize_worktree(repo["path"], payload, snapshot_bin=snapshot_bin)
    assert result.tree_commit == repo["pre"]
    assert result.tree_sha == _git(repo["path"], "rev-parse", f"{repo['pre']}^{{tree}}")
    assert sorted(p.name for p in (payload / "repo").iterdir()) == ["a.txt"]
    assert not (payload / "repo" / ".git").exists()


def test_default_go_run_and_merge_parent_fallback(tmp_path, repo):
    payload = _payload(tmp_path, tree_commit=None, merge_commit=repo["merge"])
    result = materialize_worktree(repo["path"], payload)
    assert result.tree_commit == repo["pre"]
    assert (payload / "repo" / "a.txt").read_text() == "pre"
    assert not (payload / "repo" / "pr.txt").exists()


def test_tree_drift_fails_closed(tmp_path, repo, snapshot_bin):
    bogus = "f" * 40
    fake = _wrapper(tmp_path, snapshot_bin,
                    f"sed -i 's/\"tree_sha\": \"[0-9a-f]*\"/\"tree_sha\": \"{bogus}\"/' "
                    '"$OUT/history.json"')
    payload = _payload(tmp_path, tree_commit=repo["pre"], merge_commit=repo["merge"])
    with pytest.raises(WorktreeError) as err:
        materialize_worktree(repo["path"], payload, snapshot_bin=fake)
    expected = _git(repo["path"], "rev-parse", f"{repo['pre']}^{{tree}}")
    assert repo["pre"] in str(err.value)
    assert bogus in str(err.value) and expected in str(err.value)
    assert not (payload / "repo").exists()


def test_missing_commit_fails_closed(tmp_path, repo, snapshot_bin):
    missing = "0123456789abcdef0123456789abcdef01234567"
    payload = _payload(tmp_path, tree_commit=missing, merge_commit=repo["merge"])
    with pytest.raises(WorktreeError, match=missing):
        materialize_worktree(repo["path"], payload, snapshot_bin=snapshot_bin)


def test_git_metadata_in_tree_fails_closed(tmp_path, repo, snapshot_bin):
    fake = _wrapper(tmp_path, snapshot_bin, 'mkdir "$OUT/repo/.git"')
    payload = _payload(tmp_path, tree_commit=repo["pre"], merge_commit=repo["merge"])
    with pytest.raises(WorktreeError, match=r"\.git"):
        materialize_worktree(repo["path"], payload, snapshot_bin=fake)
    assert not (payload / "repo").exists()


def test_empty_tree_fails_closed(tmp_path, repo, snapshot_bin):
    fake = _wrapper(tmp_path, snapshot_bin, 'rm -rf "$OUT/repo" && mkdir "$OUT/repo"')
    payload = _payload(tmp_path, tree_commit=repo["pre"], merge_commit=repo["merge"])
    with pytest.raises(WorktreeError, match="empty"):
        materialize_worktree(repo["path"], payload, snapshot_bin=fake)
