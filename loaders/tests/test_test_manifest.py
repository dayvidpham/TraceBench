"""PR test-case inventory and golden classification."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tracebench_corpus.cli import main
from tracebench_corpus.test_manifest import build_test_manifest


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_manifest_marks_only_added_and_modified_go_cases(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    tests = repo / "pkg" / "thing_test.go"
    tests.parent.mkdir()
    tests.write_text(
        "package pkg\n\n"
        "func TestUnchanged(t *testing.T) { t.Log(`{ func TestFake(t *testing.T) {} }`) }\n"
        "func TestMain(m *testing.M) {}\n"
        "func TestModified(t *testing.T) { t.Log(1) }\n"
        "func TestDeleted(t *testing.T) {}\n"
    )
    base = _commit(repo, "base")
    tests.write_text(
        "package pkg\n\n"
        "// func TestComment(t *testing.T) {}\n"
        "func TestUnchanged(t *testing.T) { t.Log(`{ func TestFake(t *testing.T) {} }`) }\n"
        "func TestMain(m *testing.M) {}\n"
        "func TestModified(t *testing.T) { t.Log(2) }\n"
        "func TestAdded(t *testing.T) {}\n"
    )
    other = repo / "elsewhere_test.go"
    other.write_text("package repo\nfunc TestOther(t *testing.T) {}\n")
    merged = _commit(repo, "merge")

    manifest = build_test_manifest(repo, base, merged)
    assert manifest["base_commit"] == base
    assert manifest["merge_commit"] == merged
    assert [suite["path"] for suite in manifest["suites"]] == [
        "elsewhere_test.go", "pkg/thing_test.go"
    ]
    assert manifest["suites"][0]["package_dir"] == "."
    assert manifest["suites"][1]["package_dir"] == "pkg"
    assert manifest["summary"] == {
        "suites": 2, "cases": 4, "golden_cases": 3
    }
    cases = {case["name"]: case for suite in manifest["suites"] for case in suite["cases"]}
    assert set(cases) == {"TestAdded", "TestModified", "TestOther", "TestUnchanged"}
    assert {name for name, case in cases.items() if case["golden"]} == {
        "TestAdded", "TestModified", "TestOther"
    }
    assert {case["status"] for case in cases.values()} == {"accept"}
    assert cases["TestModified"]["id"] == "pkg/thing_test.go::TestModified"

    destination = tmp_path / "out" / "test-manifest.json"
    assert main([
        "test-manifest", "--repo-dir", str(repo), "--base-commit", base,
        "--merge-commit", merged, "--dest", str(destination),
    ]) == 0
    assert json.loads(destination.read_text()) == manifest


def test_manifest_rejects_missing_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    try:
        build_test_manifest(repo, "missing", "missing")
    except ValueError as exc:
        assert "rev-parse" in str(exc)
    else:
        raise AssertionError("missing commits must fail")
