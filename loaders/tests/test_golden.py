"""Tests for golden-suite materialization from the merged commit."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import LIVE, TESTDATA, envelope, metadata
from tracebench_corpus import Corpus, TaskBuilder, build_skeleton
from tracebench_corpus.cli import main
from tracebench_corpus.golden import (
    GoldenSuiteError,
    MANIFEST_NAME,
    matches_any,
    materialize_golden_tests,
)
from tracebench_corpus.task import DEFAULT_TEST_PATTERNS

FIXTURES = yaml.safe_load((TESTDATA / "glob_patterns.yaml").read_text())
BINARY = bytes(range(256)) + b"\0\r\n\xff"


def test_glob_fixture_names_are_present() -> None:
    names = {case["name"] for case in FIXTURES["cases"]}
    assert set(FIXTURES["required_names"]) <= names


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda case: case["name"])
def test_glob_patterns(case: dict) -> None:
    patterns = DEFAULT_TEST_PATTERNS if case["patterns"] == "default" else case["patterns"]
    assert matches_any(case["path"], patterns) is case["match"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _write(repo: Path, path: str, content: str | bytes) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content)


@pytest.fixture
def pr_repo(git_repo) -> tuple[Path, str, str]:
    """A repository with a pre-PR commit and a merge commit from the fixture."""
    repo, commit = git_repo
    spec = FIXTURES["extraction"]
    for path, content in spec["base"].items():
        _write(repo, path, content)
    base = commit("base")
    merge = spec["merge"]
    for path, content in merge["add"].items():
        _write(repo, path, content)
    for old, new in merge["rename"].items():
        (repo / new).parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "mv", old, new)
    for path in merge["delete"]:
        _git(repo, "rm", "-q", path)
    _write(repo, merge["binary"], BINARY)
    for link, target in merge["symlink"].items():
        (repo / link).symlink_to(target)
    merge_commit = commit("merge")
    # The working tree diverges from the merge commit; extraction must ignore it.
    (repo / "keep_test.go").write_text("WORKING TREE\n")
    (repo / "untracked_test.go").write_text("untracked\n")
    return repo, base, merge_commit


def test_extracts_exactly_the_merged_test_files(pr_repo, tmp_path) -> None:
    repo, _base, merge = pr_repo
    dest = tmp_path / "tests"
    paths = materialize_golden_tests(repo, merge, DEFAULT_TEST_PATTERNS, dest, pr_id=f"{LIVE}#22")
    expected = FIXTURES["extraction"]["expected"]
    assert paths == sorted(expected)
    on_disk = sorted(
        str(path.relative_to(dest)) for path in dest.rglob("*") if path.is_file()
    )
    assert on_disk == sorted([*expected, MANIFEST_NAME])
    assert (dest / "keep_test.go").read_text() == "package main\n"
    assert (dest / "pkg/testdata/blob.bin").read_bytes() == BINARY
    assert not (dest / "old_name_test.go").exists()
    assert not (dest / "deleted_test.go").exists()
    assert not (dest / "link_test.go").exists()
    assert not (dest / "main.go").exists()
    manifest = json.loads((dest / MANIFEST_NAME).read_text())
    assert manifest == {
        "commit": merge,
        "patterns": list(DEFAULT_TEST_PATTERNS),
        "paths": sorted(expected),
    }


def test_zero_matches_fail_closed_naming_pr_and_patterns(pr_repo, tmp_path) -> None:
    repo, _base, merge = pr_repo
    dest = tmp_path / "tests"
    with pytest.raises(GoldenSuiteError) as excinfo:
        materialize_golden_tests(repo, merge, ["**/*.rs"], dest, pr_id=f"{LIVE}#22")
    message = str(excinfo.value)
    assert f"{LIVE}#22" in message
    assert "**/*.rs" in message
    assert merge in message
    assert not (dest / MANIFEST_NAME).exists()


def test_unknown_commit_is_actionable(pr_repo, tmp_path) -> None:
    repo, _base, _merge = pr_repo
    with pytest.raises(GoldenSuiteError, match="--repo-dir"):
        materialize_golden_tests(repo, "0" * 40, DEFAULT_TEST_PATTERNS, tmp_path / "t")


def _dump_for(tmp_path: Path, write_dump) -> Path:
    pull_requests = [
        {"id": f"{LIVE}#22", "repo": LIVE, "number": 22, "title": "target",
         "split": "test", "merged_at": "2026-09-01T00:00:00Z"},
    ]
    traces = [{"pr": f"{LIVE}#22", "session_id": "own", "method": "exact", "split": "test"}]
    return write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=[metadata("own", "2026-05-01T00:00:00Z")],
        transcripts={"own": envelope("own")},
    )


def test_task_cli_materialize_tests_and_skeleton(pr_repo, tmp_path, write_dump, capsys) -> None:
    repo, _base, merge = pr_repo
    dump = _dump_for(tmp_path, write_dump)
    index = tmp_path / "index.json"
    index.write_text(json.dumps([{"repo": LIVE, "number": 22, "merge_commit": merge}]))
    payload = tmp_path / "payload"
    assert main([
        "--corpus", str(dump), "task", f"{LIVE}#22", "--dest", str(payload),
        "--index", str(index), "--repo-dir", str(repo), "--materialize-tests",
    ]) == 0
    assert "golden tests: 7 files" in capsys.readouterr().out
    assert json.loads((payload / "task.json").read_text())["golden_tests"] == 7
    assert (payload / "tests" / "new/name_test.go").is_file()

    task = tmp_path / "task"
    skeleton = build_skeleton(payload, task)
    assert skeleton.golden_tests == 7
    assert (task / "tests" / MANIFEST_NAME).is_file()
    assert not (task / "tests" / "golden" / MANIFEST_NAME).exists()
    assert (task / "tests" / "golden" / "pkg/testdata/blob.bin").read_bytes() == BINARY


def test_repo_dir_alone_stays_ancestry_only(pr_repo, tmp_path, write_dump) -> None:
    repo, _base, merge = pr_repo
    dump = _dump_for(tmp_path, write_dump)
    payload = TaskBuilder(
        Corpus(dump), pr_index={f"{LIVE}#22": {"merge_commit": merge}}, repo_dir=repo
    ).build(f"{LIVE}#22", tmp_path / "payload")
    assert payload.golden_tests is None
    assert list((payload.path / "tests").iterdir()) == []


def test_materialize_tests_requires_repo_dir(standard_dump, tmp_path, capsys) -> None:
    assert main([
        "--corpus", str(standard_dump), "task", f"{LIVE}#22", "--dest", str(tmp_path / "p"),
        "--materialize-tests",
    ]) == 2
    assert "--materialize-tests requires --repo-dir" in capsys.readouterr().err


def test_task_build_fails_closed_on_empty_suite(git_repo, tmp_path, write_dump) -> None:
    repo, commit = git_repo
    commit("base")
    merge = commit("merge")  # only .txt files: nothing matches
    dump = _dump_for(tmp_path, write_dump)
    builder = TaskBuilder(
        Corpus(dump), pr_index={f"{LIVE}#22": {"merge_commit": merge}},
        repo_dir=repo, materialize_tests=True,
    )
    with pytest.raises(GoldenSuiteError, match=rf"{LIVE}#22"):
        builder.build(f"{LIVE}#22", tmp_path / "payload")
    assert not (tmp_path / "payload" / "task.json").exists()
