"""Tests for Harbor task skeleton generation."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from tracebench_corpus import Corpus, TaskBuilder, build_skeleton, task_slug
from tracebench_corpus.cli import main
from test_task import write_task_dump


def make_payload(tmp_path: Path) -> Path:
    """Build a payload and simulate the repository tooling filling the stubs."""
    corpus = Corpus(write_task_dump(tmp_path / "dump"))
    payload = tmp_path / "payload"
    TaskBuilder(corpus).build("peasant-labs/peasant#22", payload)
    (payload / "repo" / "main.go").write_text("package main\n")
    (payload / "tests" / "greeting_test.go").write_text("package main\n")
    return payload


def test_task_slug() -> None:
    assert task_slug("peasant-labs/peasant") == "peasant"
    assert task_slug("peasant-labs/peasant-prerelease-archive") == "peasant-archive"
    assert task_slug("Some/Repo") == "repo"


def test_skeleton_layout(tmp_path: Path) -> None:
    payload = make_payload(tmp_path)
    skeleton = build_skeleton(payload, tmp_path / "task")
    assert skeleton.task_name == "tracebench/peasant-pr-0022"
    assert skeleton.golden_tests == 1

    task = tmp_path / "task"
    instruction = (task / "instruction.md").read_text()
    assert instruction.startswith("# the task")
    assert "peasant-labs/peasant#22" in instruction
    assert "/prior-traces" in instruction

    assert (task / "environment" / "repo" / "main.go").is_file()
    assert (task / "environment" / "prior-traces" / "transcripts" / "a1.jsonl").is_file()
    assert (task / "tests" / "golden" / "greeting_test.go").is_file()
    assert "reward.txt" in (task / "tests" / "test.sh").read_text()
    assert os.access(task / "tests" / "test.sh", os.X_OK)
    assert os.access(task / "solution" / "solve.sh", os.X_OK)

    payload_copy = json.loads((task / "task-payload.json").read_text())
    assert payload_copy["pr"]["id"] == "peasant-labs/peasant#22"
    assert payload_copy["summary"]["prior_sessions"] == 2


def test_skeleton_task_toml_parses(tmp_path: Path) -> None:
    payload = make_payload(tmp_path)
    build_skeleton(payload, tmp_path / "task")
    with (tmp_path / "task" / "task.toml").open("rb") as handle:
        config = tomllib.load(handle)
    assert config["schema_version"] == "1.4"
    assert config["task"]["name"] == "tracebench/peasant-pr-0022"
    assert config["task"]["keywords"] == ["tracebench", "swe", "real-pr", "peasant"]
    assert config["metadata"]["repo"] == "peasant-labs/peasant"
    assert config["metadata"]["pr_number"] == 22
    assert config["metadata"]["split"] == "test"
    assert config["agent"]["network_mode"] == "no-network"
    assert config["verifier"]["network_mode"] == "no-network"
    assert config["environment"]["network_mode"] == "public"


def test_skeleton_fails_closed_without_golden_tests(tmp_path: Path) -> None:
    corpus = Corpus(write_task_dump(tmp_path / "dump"))
    payload = tmp_path / "payload"
    TaskBuilder(corpus).build("peasant-labs/peasant#22", payload)
    skeleton = build_skeleton(payload, tmp_path / "task")
    assert skeleton.golden_tests == 0
    assert "not materialized" in (tmp_path / "task" / "tests" / "test.sh").read_text()


def test_skeleton_cli(tmp_path: Path, capsys) -> None:
    payload = make_payload(tmp_path)
    assert main(["skeleton", "--payload", str(payload), "--dest", str(tmp_path / "cli-task")]) == 0
    output = capsys.readouterr().out
    assert "tracebench/peasant-pr-0022" in output
    assert "1 golden test files" in output

    assert main(["skeleton", "--payload", str(tmp_path / "missing"), "--dest", str(tmp_path / "x")]) == 2
    assert "not a task payload" in capsys.readouterr().err
