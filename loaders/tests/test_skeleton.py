"""Tests for Harbor task skeleton generation."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from conftest import BODY_TEXT, LIVE, TESTDATA
from tracebench_corpus import (
    MAX_BODY_CHARS,
    Corpus,
    TaskBuilder,
    build_skeleton,
    glob_to_regex,
    find_target_config,
    task_slug,
)
from tracebench_corpus.cli import main


def make_payload(tmp_path: Path, dump: Path, index: dict, *, golden: bool = True) -> Path:
    payload = tmp_path / "payload"
    TaskBuilder(Corpus(dump), pr_index=index).build(f"{LIVE}#22", payload)
    (payload / "repo" / "main.go").write_text("package main\n")
    if golden:
        (payload / "tests" / "greeting_test.go").write_text("package main\n")
    (payload / "test-manifest.json").write_text('{"suites": []}\n')
    return payload


def run_verifier(script: Path, app: Path, golden: Path, log: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "APP_DIR": str(app), "GOLDEN_DIR": str(golden), "LOG_DIR": str(log)}
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)


def test_task_slug() -> None:
    assert task_slug(LIVE) == "peasant"
    assert task_slug("peasant-labs/peasant-prerelease-archive") == "peasant-archive"
    assert task_slug("Some/Repo") == "repo"


def test_skeleton_ships_verifier_and_config(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    build_skeleton(payload, tmp_path / "task", test_command="go test -json -count=1 ./internal/...")
    tests = tmp_path / "task" / "tests"
    assert (tests / "verifier.py").is_file()
    config = json.loads((tests / "verifier-config.json").read_text())
    assert config["pr"] == f"{LIVE}#22"
    assert config["test_command"] == "go test -json -count=1 ./internal/..."
    assert config["remove_regexes"] == [glob_to_regex(p).pattern for p in config["remove_patterns"]]
    assert "verifier.py" in (tests / "test.sh").read_text()


def test_skeleton_tolerates_an_unsampled_split(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    pr_path = payload / "pr.json"
    pr = json.loads(pr_path.read_text())
    pr["split"] = None
    pr_path.write_text(json.dumps(pr))
    build_skeleton(payload, tmp_path / "task")
    task_toml = tomllib.loads((tmp_path / "task" / "task.toml").read_text())
    assert task_toml["metadata"]["split"] == ""
    assert "Benchmark split: unknown" in (tmp_path / "task" / "instruction.md").read_text()


def test_skeleton_healthcheck_warms_the_pre_pr_tree(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    build_skeleton(payload, tmp_path / "task",
                   healthcheck_command="cd /workdir/repo && go build ./...")
    environment = tomllib.loads((tmp_path / "task" / "task.toml").read_text())["environment"]
    assert environment["healthcheck"]["command"] == "cd /workdir/repo && go build ./..."
    assert environment["healthcheck"]["retries"] == 2

    build_skeleton(payload, tmp_path / "plain")
    assert "healthcheck" not in tomllib.loads((tmp_path / "plain" / "task.toml").read_text())["environment"]


def test_skeleton_uses_shared_base_image_and_runtime_data(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    skeleton = build_skeleton(payload, tmp_path / "task")
    task = tmp_path / "task"
    assert skeleton.task_name == "tracebench/peasant-pr-0022"
    assert skeleton.golden_tests == 1

    # No per-task image build: the shared base image is referenced, and the
    # task data is uploaded from environment/ at environment start.
    assert not (task / "environment" / "Dockerfile").exists()
    assert (task / "environment" / "repo" / "main.go").is_file()
    assert (task / "environment" / "prior-traces" / "transcripts" / "l1.jsonl").is_file()
    environment_files = {
        path.relative_to(task / "environment").as_posix()
        for path in (task / "environment").rglob("*")
        if path.is_file()
    }
    assert all(not name.startswith("tests/") and "golden" not in name for name in environment_files)

    # The golden suite is verifier-only.
    assert (task / "tests" / "golden" / "greeting_test.go").is_file()
    assert json.loads((task / "tests" / "test-manifest.json").read_text()) == {"suites": []}
    assert not (task / "tests" / "golden" / "test-manifest.json").exists()

    instruction = (task / "instruction.md").read_text()
    assert "/workdir/repo" in instruction
    assert "/workdir/prior-traces" in instruction
    assert "read-only" not in instruction
    assert "peasant-223" not in instruction  # no head-branch hint
    assert (task / "task-payload.json").is_file()


def test_skeleton_task_toml_parses(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    build_skeleton(payload, tmp_path / "task")
    with (tmp_path / "task" / "task.toml").open("rb") as handle:
        config = tomllib.load(handle)
    assert config["schema_version"] == "1.4"
    assert config["task"]["name"] == "tracebench/peasant-pr-0022"
    assert config["metadata"]["repo"] == LIVE
    assert config["metadata"]["pr_number"] == 22
    assert config["agent"]["network_mode"] == "no-network"
    assert config["verifier"]["network_mode"] == "no-network"
    assert config["environment"]["docker_image"] == "tracebench/task-runtime:latest"
    assert config["environment"]["workdir"] == "/workdir"
    assert config["environment"]["network_mode"] == "public"


def test_skeleton_records_target_configuration(
    tmp_path, target_config_spec, standard_dump, standard_index
) -> None:
    payload = tmp_path / "payload"
    configuration = find_target_config(target_config_spec, "opencode-gpt-medium")
    TaskBuilder(Corpus(standard_dump), pr_index=standard_index).build(
        f"{LIVE}#22", payload, target_config=configuration
    )
    build_skeleton(payload, tmp_path / "task")
    with (tmp_path / "task" / "task.toml").open("rb") as handle:
        config = tomllib.load(handle)
    assert config["metadata"]["target_configuration"] == "opencode-gpt-medium"


def test_verifier_removes_stale_tests_and_copies_golden(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    build_skeleton(payload, tmp_path / "task")
    script = tmp_path / "task" / "tests" / "test.sh"
    app = tmp_path / "app"
    (app / "pkg").mkdir(parents=True)
    (app / "pkg" / "old_test.go").write_text("stale")
    (app / "old_test.go").write_text("stale root")
    (app / "pkg" / "keep.go").write_text("keep")
    golden = tmp_path / "task" / "tests" / "golden"
    log = tmp_path / "logs"

    result = run_verifier(script, app, golden, log)
    assert result.returncode == 0
    assert not (app / "pkg" / "old_test.go").exists()
    assert not (app / "old_test.go").exists()
    assert (app / "pkg" / "keep.go").read_text() == "keep"
    assert (app / "greeting_test.go").is_file()
    # The stub manifest has no schema_version, so the verifier fails closed.
    assert float((log / "reward.txt").read_text()) == 0.0
    report = json.loads((log / "test-results.json").read_text())
    assert any("schema_version" in reason for reason in report["fail_closed_reasons"])


def test_verifier_fails_closed_without_golden(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index, golden=False)
    build_skeleton(payload, tmp_path / "task")
    script = tmp_path / "task" / "tests" / "test.sh"
    app = tmp_path / "app"
    app.mkdir()
    log = tmp_path / "logs"

    result = run_verifier(script, app, tmp_path / "task" / "tests" / "golden", log)
    assert result.returncode == 0
    assert "not materialized" in result.stderr
    assert float((log / "reward.txt").read_text()) == 0.0


def test_oracle_placeholder_exits_nonzero(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    build_skeleton(payload, tmp_path / "task")
    result = subprocess.run(
        ["bash", str(tmp_path / "task" / "solution" / "solve.sh")],
        capture_output=True, text=True,
    )
    assert result.returncode == 1


def test_skeleton_rebuild_requires_force(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    task = tmp_path / "task"
    build_skeleton(payload, task)
    (payload / "tests" / "greeting_test.go").unlink()

    with pytest.raises(ValueError, match="not empty"):
        build_skeleton(payload, task)

    rebuilt = build_skeleton(payload, task, force=True)
    assert rebuilt.golden_tests == 0
    assert not (task / "tests" / "golden" / "greeting_test.go").exists()


def test_skeleton_cli(tmp_path, standard_dump, standard_index, capsys) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    assert main(["skeleton", "--payload", str(payload), "--dest", str(tmp_path / "cli-task")]) == 0
    output = capsys.readouterr().out
    assert "tracebench/peasant-pr-0022" in output
    assert "1 golden test files" in output

    assert main(["skeleton", "--payload", str(tmp_path / "missing"), "--dest", str(tmp_path / "x")]) == 2
    assert "not a task payload" in capsys.readouterr().err


def test_skeleton_rejects_non_object_task_json(tmp_path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pr.json").write_text(json.dumps({"id": f"{LIVE}#1", "repo": LIVE, "number": 1}))
    (payload / "task.json").write_text("[]")
    with pytest.raises(ValueError, match="task.json"):
        build_skeleton(payload, tmp_path / "task")


def test_skeleton_renders_pr_body_as_goal(tmp_path, body_dump, standard_index) -> None:
    payload = tmp_path / "payload"
    TaskBuilder(Corpus(body_dump), pr_index=standard_index).build(f"{LIVE}#22", payload)
    build_skeleton(payload, tmp_path / "task")
    instruction = (tmp_path / "task" / "instruction.md").read_text()
    goal = instruction.split("## Goal")[1]
    assert BODY_TEXT in goal
    assert "TODO(task author)" not in instruction
    assert "/workdir/repo" in instruction
    assert "/workdir/prior-traces" in instruction


def test_skeleton_keeps_todo_without_body(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    pr = json.loads((payload / "pr.json").read_text())
    assert pr.get("body") is None
    build_skeleton(payload, tmp_path / "task")
    instruction = (tmp_path / "task" / "instruction.md").read_text()
    assert "Implement the change the pull request made." in instruction
    assert "## TODO(task author)" in instruction

    pr["body"] = "  \n "
    (payload / "pr.json").write_text(json.dumps(pr))
    build_skeleton(payload, tmp_path / "blank-task", force=True)
    assert "## TODO(task author)" in (tmp_path / "blank-task" / "instruction.md").read_text()


def test_skeleton_rejects_non_string_body(tmp_path, standard_dump, standard_index) -> None:
    payload = make_payload(tmp_path, standard_dump, standard_index)
    pr = json.loads((payload / "pr.json").read_text())
    pr["body"] = 123
    (payload / "pr.json").write_text(json.dumps(pr))
    with pytest.raises(ValueError, match="field `body`"):
        build_skeleton(payload, tmp_path / "task")


def test_skeleton_truncates_overlong_body(tmp_path, long_body_dump, standard_index) -> None:
    payload = tmp_path / "payload"
    TaskBuilder(Corpus(long_body_dump), pr_index=standard_index).build(f"{LIVE}#22", payload)
    pr = json.loads((payload / "pr.json").read_text())
    assert len(pr["body"]) > MAX_BODY_CHARS
    build_skeleton(payload, tmp_path / "task")
    instruction = (tmp_path / "task" / "instruction.md").read_text()
    goal = instruction.split("## Goal")[1]
    assert f"[truncated: pull request body exceeded {MAX_BODY_CHARS} characters]" in goal
    assert pr["body"] not in goal
    assert "TODO(task author)" not in instruction
