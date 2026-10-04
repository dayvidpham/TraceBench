"""Full-history truncated repo materialization."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tracebench_corpus.worktree import WorktreeError, materialize_worktree


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
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", name, date=date)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> dict:
    path = tmp_path / "src"
    path.mkdir()
    _git(path, "init", "-q")
    root = _commit(path, "root.txt", "root", "2025-12-01T00:00:00+00:00")
    pre = _commit(path, "a.txt", "pre", "2026-01-01T00:00:00+00:00")
    merge = _commit(path, "pr.txt", "pr", "2026-02-01T00:00:00+00:00")
    return {"path": path, "root": root, "pre": pre, "merge": merge}


def _payload(tmp_path: Path, **request) -> Path:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "repo-request.json").write_text(json.dumps(request))
    return payload


def _state(repo: Path) -> tuple[str, str]:
    return (_git(repo, "for-each-ref"), _git(repo, "rev-parse", "HEAD"))


def test_truncated_repo_at_tree_commit(tmp_path, repo):
    before = _state(repo["path"])
    payload = _payload(tmp_path, tree_commit=repo["pre"], merge_commit=repo["merge"])
    result = materialize_worktree(repo["path"], payload)
    out = payload / "repo"
    assert result.tree_commit == repo["pre"]
    assert result.tree_sha == _git(repo["path"], "rev-parse", f"{repo['pre']}^{{tree}}")
    assert _git(out, "rev-parse", "HEAD") == repo["pre"]
    assert _git(out, "symbolic-ref", "HEAD") == "refs/heads/main"
    expected = _git(repo["path"], "rev-list", "--count", repo["pre"])
    assert int(expected) > 1
    assert _git(out, "rev-list", "--all", "--count") == expected
    # The real ancestors ship, with their real SHAs.
    assert _git(out, "log", "--format=%H") == _git(
        repo["path"], "log", "--format=%H", repo["pre"])
    assert repo["root"] in _git(out, "log", "--format=%H").split()
    assert not (out / ".git" / "shallow").exists()
    assert _git(out, "remote") == ""
    assert subprocess.run(["git", "-C", str(out), "cat-file", "-e", repo["merge"]],
                          capture_output=True).returncode != 0
    assert _git(out, "status", "--porcelain") == ""
    assert _git(out, "fsck", "--full") == ""
    for name in ("FETCH_HEAD", "ORIG_HEAD", "logs", "objects/info/alternates"):
        assert not (out / ".git" / name).exists(), name
    assert _git(out, "config", "user.name") == "TraceBench Agent"
    assert _git(out, "config", "user.email") == "agent@tracebench.local"
    assert sorted(p.name for p in out.iterdir()) == [".git", "a.txt", "root.txt"]
    assert _state(repo["path"]) == before


def test_base_ref_selects_branch(tmp_path, repo):
    payload = _payload(tmp_path, tree_commit=repo["pre"], merge_commit=repo["merge"],
                       base_ref="refs/heads/develop")
    materialize_worktree(repo["path"], payload)
    assert _git(payload / "repo", "symbolic-ref", "HEAD") == "refs/heads/develop"
    assert _git(payload / "repo", "rev-parse", "refs/heads/develop") == repo["pre"]


def test_source_refs_and_head_unchanged(tmp_path, repo):
    _git(repo["path"], "update-ref", "refs/tags/v1", repo["pre"])
    before = _state(repo["path"])
    payload = _payload(tmp_path, tree_commit=repo["pre"], merge_commit=repo["merge"])
    materialize_worktree(repo["path"], payload)
    assert _state(repo["path"]) == before
    assert _git(payload / "repo", "tag") == ""


def test_merge_parent_fallback(tmp_path, repo):
    payload = _payload(tmp_path, tree_commit=None, merge_commit=repo["merge"])
    result = materialize_worktree(repo["path"], payload)
    assert result.tree_commit == repo["pre"]
    assert (payload / "repo" / "a.txt").read_text() == "pre"
    assert not (payload / "repo" / "pr.txt").exists()


def test_agent_can_commit(tmp_path, repo):
    payload = _payload(tmp_path, tree_commit=repo["pre"])
    materialize_worktree(repo["path"], payload)
    out = payload / "repo"
    (out / "b.txt").write_text("b")
    subprocess.run(["git", "-C", str(out), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(out), "-c", "commit.gpgsign=false", "commit", "-q",
                    "-m", "b"], check=True,
                   env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    assert _git(out, "log", "-1", "--format=%an <%ae>") == "TraceBench Agent <agent@tracebench.local>"


def test_missing_commit_fails_closed(tmp_path, repo):
    missing = "0123456789abcdef0123456789abcdef01234567"
    payload = _payload(tmp_path, tree_commit=missing, merge_commit=repo["merge"])
    with pytest.raises(WorktreeError, match=missing):
        materialize_worktree(repo["path"], payload)


def test_fix_present_fails_closed(tmp_path, repo):
    # tree_commit == merge commit: the "fix" is in the object store, so it fails closed.
    payload = _payload(tmp_path, tree_commit=repo["merge"], merge_commit=repo["merge"])
    with pytest.raises(WorktreeError, match="fix-absent") as err:
        materialize_worktree(repo["path"], payload)
    assert repo["merge"] in str(err.value)
    assert not (payload / "repo").exists()


def test_worktree_error_is_value_error():
    assert issubclass(WorktreeError, ValueError)


def test_request_missing_fails_closed(tmp_path, repo):
    payload = tmp_path / "payload"
    payload.mkdir()
    with pytest.raises(WorktreeError, match="repo-request.json is missing"):
        materialize_worktree(repo["path"], payload)


@pytest.mark.parametrize("body", ["not json", "[]"])
def test_request_invalid_fails_closed(tmp_path, repo, body):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "repo-request.json").write_text(body)
    with pytest.raises(WorktreeError, match="repo-request.json"):
        materialize_worktree(repo["path"], payload)


def test_request_unreadable_fails_closed(tmp_path, repo):
    payload = tmp_path / "payload"
    (payload / "repo-request.json").mkdir(parents=True)
    with pytest.raises(WorktreeError, match="cannot read"):
        materialize_worktree(repo["path"], payload)


def test_neither_commit_fails_closed(tmp_path, repo):
    payload = _payload(tmp_path, tree_commit=None, merge_commit=None)
    with pytest.raises(WorktreeError, match="neither tree_commit nor merge_commit"):
        materialize_worktree(repo["path"], payload)


def test_non_empty_dest_fails_closed(tmp_path, repo):
    payload = _payload(tmp_path, tree_commit=repo["pre"])
    (payload / "repo").mkdir()
    (payload / "repo" / "stale.txt").write_text("x")
    with pytest.raises(WorktreeError, match=r"repo is not empty"):
        materialize_worktree(repo["path"], payload)


def test_dest_is_file_fails_closed(tmp_path, repo):
    payload = _payload(tmp_path, tree_commit=repo["pre"])
    (payload / "repo").write_text("x")
    with pytest.raises(WorktreeError) as err:
        materialize_worktree(repo["path"], payload)
    assert str(payload / "repo") in str(err.value) and repo["pre"] in str(err.value)


def test_pre_existing_empty_dest_is_replaced(tmp_path, repo):
    payload = _payload(tmp_path, tree_commit=repo["pre"])
    (payload / "repo").mkdir()
    materialize_worktree(repo["path"], payload)
    assert sorted(p.name for p in (payload / "repo").iterdir()) == [".git", "a.txt", "root.txt"]


def test_dest_symlink_to_empty_dir_fails_closed(tmp_path, repo):
    payload = _payload(tmp_path, tree_commit=repo["pre"])
    target = tmp_path / "empty-target"
    target.mkdir()
    (payload / "repo").symlink_to(target)
    with pytest.raises(WorktreeError, match="cannot replace the empty destination") as err:
        materialize_worktree(repo["path"], payload)
    assert repo["pre"] in str(err.value)
