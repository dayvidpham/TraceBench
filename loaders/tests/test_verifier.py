"""Tests for the golden-suite verifier: stream parsing, grading, and the full run."""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from conftest import TESTDATA
from tracebench_corpus import DEFAULT_TEST_PATTERNS, glob_to_regex, grade, parse_go_test_json
from tracebench_corpus.verifier import OUTCOMES, main, remove_test_files

STREAMS = TESTDATA / "go_test_json"
CASES = yaml.safe_load((STREAMS / "cases.yaml").read_text())
PARITY = yaml.safe_load((TESTDATA / "test_pattern_parity.yaml").read_text())


def fixture_manifest(cases: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "base_commit": "base",
        "merge_commit": "merge",
        "summary": {"suites": len(cases), "cases": len(cases), "golden_cases": sum(c["golden"] for c in cases)},
        "suites": [
            {"path": case["id"].split("::")[0], "cases": [{**case, "name": case["id"].split("::")[1]}]}
            for case in cases
        ],
    }


def test_stream_cases_present() -> None:
    names = {case["name"] for case in CASES["cases"]}
    assert set(CASES["required_names"]) <= names


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["name"])
def test_grade_stream(case: dict) -> None:
    manifest = fixture_manifest(CASES["manifest_cases"])
    stream = (STREAMS / case["stream"]).read_text()
    reward, report = grade(manifest, stream, CASES["module"])
    assert reward == pytest.approx(case["reward"])
    assert {entry["name"]: entry["outcome"] for entry in report["entries"]} == case["outcomes"]
    assert all(entry["outcome"] in OUTCOMES for entry in report["entries"])
    assert report["total_cases"] == 2
    assert report["passed"] == sum(o == "pass" for o in case["outcomes"].values())
    if "reason" in case:
        assert any(case["reason"] in reason for reason in report["fail_closed_reasons"])
    else:
        assert report["fail_closed_reasons"] == []


def test_subtests_never_counted() -> None:
    parsed = parse_go_test_json((STREAMS / "subtests.jsonl").read_text())
    assert all("/" not in name for _, name in parsed.tests)


def test_report_schema_fields() -> None:
    manifest = fixture_manifest(CASES["manifest_cases"])
    _, report = grade(manifest, (STREAMS / "fail.jsonl").read_text(), CASES["module"], pr="o/r#1", exit_code=1)
    assert report["schema_version"] == 1
    assert report["pr"] == "o/r#1"
    assert report["manifest_schema_version"] == 1
    assert report["exit_code"] == 1
    assert report["golden_flagged"] == 1
    assert report["golden_flagged_passed"] == 0
    assert report["failed_test_ids"] == ["pkg/sub/b_test.go::TestB"]
    assert report["entries"][1] == {
        "id": "pkg/sub/b_test.go::TestB", "package_dir": "pkg/sub", "name": "TestB",
        "golden": True, "outcome": "fail",
    }


def test_missing_manifest_fails_closed() -> None:
    reward, report = grade(None, (STREAMS / "pass.jsonl").read_text(), CASES["module"])
    assert reward == 0.0
    assert "manifest is missing" in report["fail_closed_reasons"][0]


def test_unknown_manifest_schema_fails_closed() -> None:
    manifest = {**fixture_manifest(CASES["manifest_cases"]), "schema_version": 99}
    reward, report = grade(manifest, (STREAMS / "pass.jsonl").read_text(), CASES["module"])
    assert reward == 0.0
    assert "schema_version 99" in report["fail_closed_reasons"][0]


def test_zero_cases_is_a_build_error() -> None:
    reward, report = grade(fixture_manifest([]), (STREAMS / "pass.jsonl").read_text(), CASES["module"])
    assert reward == 0.0
    assert "summary.cases" in report["fail_closed_reasons"][0]


def test_killed_command_fails_closed() -> None:
    manifest = fixture_manifest(CASES["manifest_cases"])
    reward, report = grade(
        manifest, (STREAMS / "pass.jsonl").read_text(), CASES["module"], failure="killed by signal 9"
    )
    assert reward == 0.0
    assert report["fail_closed_reasons"] == ["killed by signal 9"]


def test_parity_patterns_present() -> None:
    assert set(PARITY["required_patterns"]) == set(DEFAULT_TEST_PATTERNS)
    assert {case["pattern"] for case in PARITY["cases"]} == set(DEFAULT_TEST_PATTERNS)


def _retired_find_path(pattern: str, path: str) -> bool:
    """Reference oracle: the retired ``find -path`` translation and its matching.

    ``find -path`` lets ``*`` cross ``/``, as ``fnmatch`` does.
    """
    translated = "*" + pattern[3:] if pattern.startswith("**/") else pattern
    translated = translated.replace("**", "*")
    return fnmatch.fnmatchcase("/app/" + path, "/app/" + translated)


@pytest.mark.parametrize("case", PARITY["cases"], ids=lambda c: f"{c['pattern']}:{c['path']}")
def test_canonical_matcher_parity(case: dict) -> None:
    assert bool(glob_to_regex(case["pattern"]).match(case["path"])) is case["match"]
    assert _retired_find_path(case["pattern"], case["path"]) is case["match"]


def test_remove_test_files_skips_git(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "x_test.go").write_text("keep")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x_test.go").write_text("drop")
    removed = remove_test_files(tmp_path, [glob_to_regex("**/*_test.go").pattern])
    assert removed == ["a/x_test.go"]
    assert (tmp_path / ".git" / "x_test.go").exists()


def test_grade_cli(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(fixture_manifest(CASES["manifest_cases"])))
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module example.com/demo\n\ngo 1.22\n")
    log = tmp_path / "log"
    assert main(["grade", "--manifest", str(manifest), "--stream", str(STREAMS / "fail.jsonl"),
                 "--go-mod", str(go_mod), "--log-dir", str(log)]) == 0
    assert float((log / "reward.txt").read_text()) == 0.5
    assert sorted(p.name for p in log.iterdir()) == ["reward.txt", "test-results.json"]


# --- End to end: a real Go module, a golden overlay, and a real `go test -json` run.

GO = shutil.which("go")

GREET = textwrap.dedent(
    """\
    package demo

    func Greet(name string) string { return "hi " + name }
    """
)
GREET_FIXED = GREET.replace('"hi "', '"hello "')
GOLDEN_TEST = textwrap.dedent(
    """\
    package demo

    import "testing"

    func TestGreet(t *testing.T) {
    \tif got := Greet("x"); got != "hello x" {
    \t\tt.Fatalf("Greet = %q", got)
    \t}
    }

    func TestStable(t *testing.T) {
    \tt.Run("sub", func(t *testing.T) {})
    }
    """
)


@pytest.mark.skipif(GO is None, reason="the Go toolchain is required for the end-to-end verifier test")
def test_end_to_end_reward_rises_after_fix(tmp_path) -> None:
    from tracebench_corpus.skeleton import _test_sh  # the script the skeleton ships
    from tracebench_corpus import verifier

    tests_dir = tmp_path / "tests"
    (tests_dir / "golden").mkdir(parents=True)
    (tests_dir / "golden" / "greet_test.go").write_text(GOLDEN_TEST)
    (tests_dir / "verifier.py").write_bytes(Path(verifier.__file__).read_bytes())
    (tests_dir / "test-manifest.json").write_text(json.dumps({
        "schema_version": 1, "base_commit": "b", "merge_commit": "m",
        "summary": {"suites": 1, "cases": 2, "golden_cases": 1},
        "suites": [{"path": "greet_test.go", "package_dir": ".", "cases": [
            {"id": "greet_test.go::TestGreet", "name": "TestGreet", "golden": True},
            {"id": "greet_test.go::TestStable", "name": "TestStable", "golden": False},
        ]}],
    }))
    (tests_dir / "verifier-config.json").write_text(json.dumps({
        "pr": "o/demo#1", "test_command": "go test -json -count=1 ./...",
        "remove_regexes": [glob_to_regex("**/*_test.go").pattern],
    }))
    script = tests_dir / "test.sh"
    script.write_text(_test_sh(300.0))

    app = tmp_path / "repo"
    app.mkdir()
    (app / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n")
    (app / "greet.go").write_text(GREET)
    (app / "stale_test.go").write_text("package demo\n\nfunc broken( {\n")
    log = tmp_path / "logs"
    env = {**os.environ, "APP_DIR": str(app), "LOG_DIR": str(log), "GOFLAGS": "-mod=mod",
           "GOCACHE": str(tmp_path / "gocache")}

    def verify() -> tuple[float, dict]:
        result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return float((log / "reward.txt").read_text()), json.loads((log / "test-results.json").read_text())

    reward, report = verify()
    assert not (app / "stale_test.go").exists()
    assert reward == pytest.approx(0.5)
    assert report["exit_code"] != 0
    assert report["failed_test_ids"] == ["greet_test.go::TestGreet"]
    assert report["fail_closed_reasons"] == []

    (app / "greet.go").write_text(GREET_FIXED)
    reward, report = verify()
    assert reward == pytest.approx(1.0)
    assert report["exit_code"] == 0
    assert report["passed"] == 2
