"""Oracle generation: merge diff, solve.sh, equivalence check, placement, CLI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tracebench_corpus import oracle as oracle_module
from tracebench_corpus.cli import main
from tracebench_corpus.oracle import build_oracle
from tracebench_corpus.skeleton import build_skeleton

PR_ID = "peasant-labs/peasant#22"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def pr_repo(git_repo):
    """A repository whose second commit edits, adds, deletes, and renames files."""
    repo, commit = git_repo
    (repo / "keep.go").write_text("package main\n\nfunc A() int { return 1 }\n")
    (repo / "gone.txt").write_text("remove me\n")
    (repo / "old_name.txt").write_text("renamed content\n" * 5)
    tree_commit = commit("base")
    (repo / "keep.go").write_text("package main\n\nfunc A() int { return 2 }\n")
    (repo / "gone.txt").unlink()
    (repo / "old_name.txt").rename(repo / "new_name.txt")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "added.go").write_text("package pkg\n")
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    merge_commit = commit("merge")
    return repo, tree_commit, merge_commit


def _payload(tmp_path: Path, tree_commit: str | None, merge_commit: str) -> Path:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pr.json").write_text(json.dumps({
        "id": PR_ID, "repo": "peasant-labs/peasant", "number": 22, "title": "the task",
    }))
    (payload / "task.json").write_text(json.dumps({"pr": PR_ID, "prior_sessions": 0}))
    (payload / "repo-request.json").write_text(json.dumps({
        "merge_commit": merge_commit, "tree_commit": tree_commit,
    }))
    return payload


def _pre_pr_copy(repo: Path, tree_commit: str, dest: Path) -> Path:
    """A plain (non-git) copy of the tree at ``tree_commit``, like environment/repo."""
    dest.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", tree_commit], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    return dest


def _run_solve(solution: Path, app_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(solution / "solve.sh")],
        env={**os.environ, "APP_DIR": str(app_dir)},
        capture_output=True, text=True,
    )


def test_patch_applies_cleanly_and_reproduces_merge_tree(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    result = build_oracle(repo, tree_commit, merge_commit, "true")

    expected = _git(repo, "rev-parse", f"{merge_commit}^{{tree}}")
    assert result.verified is True
    assert result.applied_tree == expected
    assert set(result.changed_files) == {
        "keep.go", "gone.txt", "old_name.txt", "new_name.txt", "pkg/added.go", "blob.bin",
        "merge.txt",  # written by the git_repo fixture's commit()
    }
    # Independently: the patch applies to a fresh worktree at tree_commit.
    worktree = tmp_path / "check"
    _git(repo, "worktree", "add", "--detach", "-q", str(worktree), tree_commit)
    patch = tmp_path / "oracle.patch"
    patch.write_bytes(result.patch)
    _git(worktree, "apply", "--check", "--index", str(patch))
    _git(worktree, "apply", "--index", str(patch))
    assert _git(worktree, "write-tree") == expected
    # No worktree is leaked by the equivalence check.
    assert "tracebench-oracle-" not in _git(repo, "worktree", "list")


def test_equivalence_mismatch_fails_closed_naming_both_trees(pr_repo, monkeypatch, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    real_git = oracle_module._git
    # Fault injection is necessary: a real `git diff A B` always reproduces
    # B's tree, so the mismatch branch is unreachable without corrupting the
    # patch between generation and verification.

    def truncated_diff(repo_dir, *args, input=None):
        out = real_git(repo_dir, *args, input=input)
        if args[:2] == ("diff", "--binary"):
            # Keep only the first file's hunk: applies cleanly, wrong tree.
            return out.split(b"\ndiff --git ")[0] + b"\n"
        return out

    monkeypatch.setattr(oracle_module, "_git", truncated_diff)
    with pytest.raises(ValueError) as excinfo:
        build_oracle(repo, tree_commit, merge_commit, None)
    message = str(excinfo.value)
    assert "equivalence check failed" in message
    assert _git(repo, "rev-parse", f"{merge_commit}^{{tree}}") in message
    assert tree_commit in message

    payload = _payload(tmp_path, tree_commit, merge_commit)
    assert main(["oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload)]) == 2
    assert not (payload / "solution").exists()
    assert "oracle" not in json.loads((payload / "task.json").read_text())


def test_cli_writes_solution_and_task_oracle_block(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)

    code = main([
        "oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload),
        "--build-command", "test -f pkg/added.go",
    ])

    assert code == 0
    assert (payload / "solution" / "oracle.patch").read_bytes() == subprocess.run(
        ["git", "-C", str(repo), "diff", "--binary", "--full-index", "--no-color",
         "--no-ext-diff", tree_commit, merge_commit],
        capture_output=True, check=True,
    ).stdout
    summary = json.loads((payload / "task.json").read_text())
    assert summary["prior_sessions"] == 0
    assert summary["oracle"] == {
        "patch": "solution/oracle.patch",
        "tree_commit": tree_commit,
        "merge_commit": merge_commit,
        "changed_files": 7,
        "applied_tree": _git(repo, "rev-parse", f"{merge_commit}^{{tree}}"),
        "verified": True,
    }


def test_null_tree_commit_falls_back_to_merge_parent(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, None, merge_commit)
    assert main(["oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload)]) == 0
    assert json.loads((payload / "task.json").read_text())["oracle"]["tree_commit"] == tree_commit


def test_solve_sh_applies_patch_and_builds(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    assert main([
        "oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload),
        "--build-command", "grep -q 'return 2' keep.go",
    ]) == 0
    app = _pre_pr_copy(repo, tree_commit, tmp_path / "app")

    result = _run_solve(payload / "solution", app)

    assert result.returncode == 0, result.stderr
    assert (app / "pkg" / "added.go").is_file()
    assert not (app / "gone.txt").exists()
    assert (app / "blob.bin").read_bytes() == bytes(range(256))


def test_solve_sh_wrong_patch_exits_nonzero(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    assert main(["oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload)]) == 0
    patch = payload / "solution" / "oracle.patch"
    patch.write_bytes(patch.read_bytes().replace(b"return 1", b"return 9"))
    app = _pre_pr_copy(repo, tree_commit, tmp_path / "app")

    result = _run_solve(payload / "solution", app)

    assert result.returncode != 0
    assert "does not apply" in result.stderr
    assert (app / "gone.txt").exists()


def test_solve_sh_failing_build_exits_nonzero(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    assert main([
        "oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload),
        "--build-command", "exit 3",
    ]) == 0
    app = _pre_pr_copy(repo, tree_commit, tmp_path / "app")

    result = _run_solve(payload / "solution", app)

    assert result.returncode != 0
    assert "build command failed" in result.stderr


def test_skeleton_places_oracle_in_solution_only(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    assert main(["oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload)]) == 0

    task = build_skeleton(payload, tmp_path / "task").path

    assert (task / "solution" / "oracle.patch").read_bytes() == (
        payload / "solution" / "oracle.patch"
    ).read_bytes()
    solve = task / "solution" / "solve.sh"
    assert solve.read_text() == (payload / "solution" / "solve.sh").read_text()
    assert solve.stat().st_mode & 0o111
    patch_bytes = (payload / "solution" / "oracle.patch").read_bytes()
    for area in ("environment", "tests"):
        for path in (task / area).rglob("*"):
            assert path.name not in {"oracle.patch", "solve.sh"}, path
            if path.is_file():
                assert path.read_bytes() != patch_bytes, path


def test_skeleton_without_oracle_keeps_placeholder(tmp_path):
    payload = _payload(tmp_path, "a" * 40, "b" * 40)
    task = build_skeleton(payload, tmp_path / "task").path
    assert not (task / "solution" / "oracle.patch").exists()
    assert "oracle solution not implemented" in (task / "solution" / "solve.sh").read_text()


def test_cli_missing_repo_request_exits_2(git_repo, tmp_path, capsys):
    repo, _ = git_repo
    payload = tmp_path / "empty"
    payload.mkdir()
    assert main(["oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload)]) == 2
    assert "repo-request.json" in capsys.readouterr().err


def test_cli_unknown_commit_exits_2(pr_repo, tmp_path, capsys):
    repo, tree_commit, _ = pr_repo
    payload = _payload(tmp_path, tree_commit, "f" * 40)
    assert main(["oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload)]) == 2
    err = capsys.readouterr().err
    assert "f" * 40 in err and "--repo-dir" in err


def test_cli_build_command_and_spec_are_exclusive(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    with pytest.raises(SystemExit) as excinfo:
        main([
            "oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload),
            "--build-command", "true", "--spec", "spec.yaml",
        ])
    assert excinfo.value.code == 2


def test_cli_spec_supplies_build_command_to_solve_sh(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    spec = Path(__file__).parent / "testdata" / "oracle_spec.yaml"
    assert main([
        "oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload), "--spec", str(spec),
    ]) == 0
    app = _pre_pr_copy(repo, tree_commit, tmp_path / "app")

    result = _run_solve(payload / "solution", app)

    assert result.returncode == 0, result.stderr
    assert (app / "spec-build-ran").is_file()


def test_cli_spec_without_matching_repo_exits_2(pr_repo, tmp_path, capsys):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    spec = Path(__file__).parent / "testdata" / "oracle_spec.yaml"
    assert main([
        "oracle", "other/repo#1", "--repo-dir", str(repo), "--payload", str(payload),
        "--spec", str(spec),
    ]) == 2
    assert "no repository spec matches" in capsys.readouterr().err


def test_cli_invalid_json_repo_request_exits_2(pr_repo, tmp_path, capsys):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    (payload / "repo-request.json").write_text("{not json")
    assert main(["oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload)]) == 2
    assert "not valid JSON" in capsys.readouterr().err
    assert not (payload / "solution").exists()


def test_cli_empty_repo_request_names_merge_commit(pr_repo, tmp_path, capsys):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    (payload / "repo-request.json").write_text("{}")
    assert main(["oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload)]) == 2
    assert "merge_commit" in capsys.readouterr().err


def test_cli_payload_without_task_json_exits_2(pr_repo, tmp_path, capsys):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    (payload / "task.json").unlink()
    assert main(["oracle", PR_ID, "--repo-dir", str(repo), "--payload", str(payload)]) == 2
    assert "task.json" in capsys.readouterr().err
    assert not (payload / "solution").exists()


def test_cli_pr_mismatch_with_payload_exits_2(pr_repo, tmp_path, capsys):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    request = json.loads((payload / "repo-request.json").read_text())
    request.update(pr=PR_ID, repo="peasant-labs/peasant")
    (payload / "repo-request.json").write_text(json.dumps(request))
    assert main(["oracle", "peasant-labs/peasant#99", "--repo-dir", str(repo),
                 "--payload", str(payload)]) == 2
    err = capsys.readouterr().err
    assert PR_ID in err and "peasant-labs/peasant#99" in err
    assert not (payload / "solution").exists()


def test_cli_spec_repo_comes_from_payload(pr_repo, tmp_path):
    repo, tree_commit, merge_commit = pr_repo
    payload = _payload(tmp_path, tree_commit, merge_commit)
    request = json.loads((payload / "repo-request.json").read_text())
    request.update(pr="mirror/fork#22", repo="peasant-labs/peasant")
    (payload / "repo-request.json").write_text(json.dumps(request))
    spec = Path(__file__).parent / "testdata" / "oracle_spec.yaml"
    assert main(["oracle", "mirror/fork#22", "--repo-dir", str(repo),
                 "--payload", str(payload), "--spec", str(spec)]) == 0
    assert "spec-build-ran" in (payload / "solution" / "solve.sh").read_text()


def test_identical_commits_yield_empty_verified_patch(pr_repo):
    repo, _, merge_commit = pr_repo
    result = build_oracle(repo, merge_commit, merge_commit, None)
    assert result.patch == b""
    assert result.changed_files == ()
    assert result.verified is True
    assert result.applied_tree == _git(repo, "rev-parse", f"{merge_commit}^{{tree}}")


def test_non_utf8_filename_does_not_raise(git_repo):
    repo, commit = git_repo
    base = commit("base")
    name = b"caf\xe9.txt"
    with open(os.path.join(os.fsencode(repo), name), "wb") as handle:
        handle.write(b"x\n")
    merge = commit("merge")
    result = build_oracle(repo, base, merge, None)
    assert os.fsdecode(name) in result.changed_files
