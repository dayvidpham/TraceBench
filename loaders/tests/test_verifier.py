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
from tracebench_corpus import verifier
from tracebench_corpus.verifier import DEFAULT_TEST_COMMAND, OUTCOMES, main, read_module_path, remove_test_files, run

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
    for key, outcome in (("passed", "pass"), ("failed", "fail"), ("skipped", "skip"), ("missing", "missing")):
        assert report[key] == sum(o == outcome for o in case["outcomes"].values())
    assert report["failed_test_ids"] == case["failed_test_ids"]
    if "reason" in case:
        assert any(case["reason"] in reason for reason in report["fail_closed_reasons"])
    else:
        assert report["fail_closed_reasons"] == []


def test_subtests_never_counted() -> None:
    parsed = parse_go_test_json((STREAMS / "subtests.jsonl").read_text())
    assert all("/" not in name for _, name in parsed.tests)


REPORT_KEYS = {
    "schema_version", "pr", "base_commit", "merge_commit", "manifest_schema_version", "test_command",
    "exit_code", "duration_sec", "total_cases", "passed", "failed", "skipped", "missing",
    "golden_flagged", "golden_flagged_passed", "failed_test_ids", "reward", "fail_closed_reasons", "entries",
}


def test_report_schema_fields() -> None:
    manifest = fixture_manifest(CASES["manifest_cases"])
    _, report = grade(
        manifest, (STREAMS / "fail.jsonl").read_text(), CASES["module"], pr="o/r#1", exit_code=1,
        test_command="go test -json ./x/...", duration_sec=1.23456,
    )
    assert set(report) == REPORT_KEYS
    assert report["base_commit"] == "base"
    assert report["merge_commit"] == "merge"
    assert report["test_command"] == "go test -json ./x/..."
    assert report["duration_sec"] == 1.235
    assert (report["failed"], report["skipped"], report["missing"]) == (1, 0, 0)
    assert report["reward"] == 0.5
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


@pytest.mark.parametrize("case", PARITY["divergences"], ids=lambda c: f"{c['pattern']}:{c['path']}")
def test_canonical_matcher_divergence(case: dict) -> None:
    assert bool(glob_to_regex(case["pattern"]).match(case["path"])) is case["canonical"]
    assert _retired_find_path(case["pattern"], case["path"]) is case["retired"]


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


@pytest.mark.parametrize(
    ("text", "module"),
    [
        ("module example.com/demo // the demo\n", "example.com/demo"),
        ('module "example.com/q"\n', "example.com/q"),
        ("// module example.com/nope\nmodule example.com/yes\n", "example.com/yes"),
    ],
)
def test_read_module_path(tmp_path, text: str, module: str) -> None:
    (tmp_path / "go.mod").write_text(text)
    assert read_module_path(tmp_path / "go.mod") == module


def test_read_module_path_bare_keyword(tmp_path) -> None:
    (tmp_path / "go.mod").write_text("module\t// nothing\n")
    with pytest.raises(ValueError, match="no module path"):
        read_module_path(tmp_path / "go.mod")


def test_default_test_command_is_single_sourced(tmp_path, standard_dump, standard_index) -> None:
    from test_skeleton import make_payload
    from tracebench_corpus.repository_spec import DEFAULT_REPOSITORY_SPECS
    from tracebench_corpus.skeleton import build_skeleton

    assert DEFAULT_TEST_COMMAND == "go test -json -count=1 ./..."
    assert {spec.test_command for spec in DEFAULT_REPOSITORY_SPECS} == {DEFAULT_TEST_COMMAND}
    build_skeleton(make_payload(tmp_path, standard_dump, standard_index), tmp_path / "task")
    config = json.loads((tmp_path / "task" / "tests" / "verifier-config.json").read_text())
    assert config["test_command"] == "go test -json -count=1 ./..."


# --- run(): fail-closed paths that never need a Go toolchain.


def run_workspace(tmp_path, test_command: str | None, *, manifest: object = "default", config: str | None = None):
    tests_dir = tmp_path / "tests"
    (tests_dir / "golden").mkdir(parents=True)
    (tests_dir / "golden" / "a_test.go").write_text("package demo\n")
    if manifest == "default":
        manifest = fixture_manifest(CASES["manifest_cases"])
    if manifest is not None:
        (tests_dir / "test-manifest.json").write_text(json.dumps(manifest))
    (tests_dir / "verifier-config.json").write_text(
        config if config is not None else json.dumps({"pr": "o/r#1", "test_command": test_command})
    )
    app = tmp_path / "repo"
    app.mkdir()
    (app / "go.mod").write_text("module example.com/demo\n")
    return {"tests_dir": tests_dir, "app_dir": app, "golden_dir": tests_dir / "golden", "log_dir": tmp_path / "log"}


def read_report(log: Path) -> tuple[float, dict]:
    return float((log / "reward.txt").read_text()), json.loads((log / "test-results.json").read_text())


def test_run_timeout_fails_closed(tmp_path) -> None:
    dirs = run_workspace(tmp_path, "echo partial-out; echo partial-err >&2; exec sleep 30")
    assert run(**dirs, timeout_sec=0.5) == 0.0
    reward, report = read_report(dirs["log_dir"])
    assert reward == 0.0
    assert any("timed out after 0.5s" in reason for reason in report["fail_closed_reasons"])
    assert report["total_cases"] == 2
    assert (dirs["log_dir"] / "test-stdout.jsonl").read_text() == "partial-out\n"
    assert (dirs["log_dir"] / "test-stderr.txt").read_text() == "partial-err\n"


def test_run_signal_kill_fails_closed(tmp_path) -> None:
    stream = (STREAMS / "pass.jsonl").as_posix()
    dirs = run_workspace(tmp_path, f"cat {stream}; kill -TERM $$")
    assert run(**dirs) == 0.0
    reward, report = read_report(dirs["log_dir"])
    assert reward == 0.0
    assert report["fail_closed_reasons"] == [
        f"test command `cat {stream}; kill -TERM $$` was killed by signal 15"
    ]
    assert report["passed"] == 2  # the stream was complete; the kill alone fails it closed


def test_run_missing_manifest_skips_test_command(tmp_path) -> None:
    dirs = run_workspace(tmp_path, f"touch {tmp_path}/ran", manifest=None)
    assert run(**dirs) == 0.0
    _, report = read_report(dirs["log_dir"])
    assert report["fail_closed_reasons"] == [verifier.MISSING_MANIFEST]
    assert not (tmp_path / "ran").exists()


def test_run_bad_manifest_shape_writes_report(tmp_path) -> None:
    bad = {**fixture_manifest(CASES["manifest_cases"]), "suites": [{"cases": [{"no": "id"}]}]}
    dirs = run_workspace(tmp_path, f"touch {tmp_path}/ran", manifest=bad)
    assert run(**dirs) == 0.0
    _, report = read_report(dirs["log_dir"])
    assert any("has no `<path>::<name>` id" in reason for reason in report["fail_closed_reasons"])


@pytest.mark.parametrize("config", ["{not json", "[1, 2]"])
def test_run_bad_config_writes_report(tmp_path, config: str) -> None:
    dirs = run_workspace(tmp_path, None, config=config)
    assert run(**dirs) == 0.0
    _, report = read_report(dirs["log_dir"])
    assert any("verifier config" in reason for reason in report["fail_closed_reasons"])


def test_run_crash_writes_minimal_report(tmp_path, monkeypatch) -> None:
    dirs = run_workspace(tmp_path, "true")

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(verifier, "overlay", boom)
    with pytest.raises(RuntimeError):
        main(["run", "--tests-dir", str(dirs["tests_dir"]), "--app-dir", str(dirs["app_dir"]),
              "--log-dir", str(dirs["log_dir"])])
    reward, report = read_report(dirs["log_dir"])
    assert reward == 0.0
    assert report == {
        "schema_version": 1, "reward": 0.0,
        "fail_closed_reasons": ["verifier crashed: RuntimeError: disk on fire"],
    }


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
