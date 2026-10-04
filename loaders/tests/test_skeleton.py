"""Tests for Harbor task skeleton generation."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from conftest import LIVE, TESTDATA
from tracebench_corpus import (
    Corpus,
    TaskBuilder,
    build_skeleton,
    find_path_pattern,
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


def test_find_path_pattern() -> None:
    assert find_path_pattern("**/*_test.go") == "*_test.go"
    assert find_path_pattern("**/testdata/**") == "*testdata/*"
    assert find_path_pattern("**/*.test.ts") == "*.test.ts"
    assert find_path_pattern("*.spec.ts") == "*.spec.ts"


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
    assert (task / "environment" / "prior-traces" / "transcripts" / "a1.jsonl").is_file()
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
    assert config["environment"]["docker_image"] == "tracebench/peasant-base:latest"
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
    assert (log / "reward.txt").read_text().strip() == "0"


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
    assert (log / "reward.txt").read_text().strip() == "0"


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
