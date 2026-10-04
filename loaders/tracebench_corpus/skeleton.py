"""Generate Harbor task skeletons from assembled task payloads.

A skeleton is a Harbor task directory:

- ``task.toml`` - task identity, metadata, timeouts, and network policy
- ``instruction.md`` - agent instructions (scaffolded from the pull request)
- ``environment/`` - Dockerfile plus the pre-PR repository and prior traces
- ``solution/solve.sh`` - oracle placeholder
- ``tests/test.sh`` - verifier that overlays the golden suite and runs it

The pre-PR repository and the golden tests come from the task payload's
integration points (``repo/`` and ``tests/``); an empty payload directory
produces a skeleton whose verifier fails closed until the repository tooling
materializes them.
"""

from __future__ import annotations

import json
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Skeleton:
    """A generated Harbor task skeleton."""

    pr_id: str
    task_name: str
    path: Path
    golden_tests: int


def task_slug(repo: str) -> str:
    """Registry-safe task slug for a repository."""
    name = repo.split("/")[-1]
    suffix = "-prerelease-archive"
    if name.endswith(suffix):
        name = name[: -len(suffix)] + "-archive"
    return name.lower()


def build_skeleton(
    payload_dir: str | Path,
    dest: str | Path,
    *,
    org: str = "tracebench",
    base_image: str = "ubuntu:24.04",
    task_version: str = "1.0.0",
    agent_timeout_sec: float = 3600.0,
    verifier_timeout_sec: float = 3600.0,
    build_timeout_sec: float = 1800.0,
) -> Skeleton:
    """Write a Harbor task skeleton from a task payload directory."""
    payload = Path(payload_dir)
    pr_path = payload / "pr.json"
    if not pr_path.is_file():
        raise ValueError(f"not a task payload: missing {pr_path}")
    pr: dict[str, Any] = json.loads(pr_path.read_text())
    summary: dict[str, Any] = {}
    if (payload / "task.json").is_file():
        summary = json.loads((payload / "task.json").read_text())

    dest = Path(dest)
    (dest / "environment").mkdir(parents=True, exist_ok=True)
    (dest / "solution").mkdir(parents=True, exist_ok=True)
    (dest / "tests" / "golden").mkdir(parents=True, exist_ok=True)

    task_name = f"{org}/{task_slug(pr['repo'])}-pr-{pr['number']:04d}"
    pr_id = pr["id"]

    (dest / "task.toml").write_text(
        _task_toml(
            task_name=task_name,
            pr=pr,
            summary=summary,
            task_version=task_version,
            agent_timeout_sec=agent_timeout_sec,
            verifier_timeout_sec=verifier_timeout_sec,
            build_timeout_sec=build_timeout_sec,
        )
    )
    (dest / "instruction.md").write_text(_instruction(pr, summary))
    (dest / "environment" / "Dockerfile").write_text(_dockerfile(base_image))

    _copy_tree(payload / "repo", dest / "environment" / "repo")
    _copy_tree(payload / "prior-traces", dest / "environment" / "prior-traces")
    golden_tests = _copy_tree(payload / "tests", dest / "tests" / "golden")

    _write_script(dest / "solution" / "solve.sh", _solve_sh(pr_id))
    _write_script(dest / "tests" / "test.sh", _test_sh())

    (dest / "task-payload.json").write_text(
        json.dumps({"pr": pr, "summary": summary}, indent=2) + "\n"
    )
    return Skeleton(pr_id=pr_id, task_name=task_name, path=dest, golden_tests=golden_tests)


def _copy_tree(source: Path, dest: Path) -> int:
    """Copy a payload directory if it exists; return the number of files."""
    if not source.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        return 0
    shutil.copytree(source, dest, dirs_exist_ok=True)
    return sum(1 for path in dest.rglob("*") if path.is_file())


def _write_script(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _task_toml(
    *,
    task_name: str,
    pr: dict[str, Any],
    summary: dict[str, Any],
    task_version: str,
    agent_timeout_sec: float,
    verifier_timeout_sec: float,
    build_timeout_sec: float,
) -> str:
    title = pr.get("title", "")
    description = f"Implement {pr['id']}: {title}"
    keywords = ", ".join(
        _toml_string(word)
        for word in ("tracebench", "swe", "real-pr", task_slug(pr["repo"]))
    )
    lines = [
        'schema_version = "1.4"',
        "artifacts = []",
        "",
        "[task]",
        f"name = {_toml_string(task_name)}",
        f"version = {_toml_string(task_version)}",
        f"description = {_toml_string(description)}",
        f"keywords = [{keywords}]",
        "",
        "[[task.authors]]",
        'name = "TraceBench"',
        "",
        "[metadata]",
        'category = "programming"',
        f"repo = {_toml_string(pr['repo'])}",
        f"pr_url = {_toml_string(pr.get('url', ''))}",
        f"pr_number = {pr['number']}",
        f"split = {_toml_string(pr.get('split', ''))}",
        f"prior_sessions = {int(summary.get('prior_sessions', 0))}",
        "difficulty_explanation = "
        + _toml_string(
            "Real merged pull request; implement the change against the pre-PR "
            "repository using prior agent traces as context."
        ),
        "",
        "[agent]",
        f"timeout_sec = {agent_timeout_sec}",
        "# Agent runs offline: no fetching the merged change or its tests.",
        'network_mode = "no-network"',
        "",
        "[verifier]",
        f"timeout_sec = {verifier_timeout_sec}",
        'network_mode = "no-network"',
        "",
        "[environment]",
        "# Baseline is public so image setup can install dependencies; the",
        "# agent and verifier phases above lock it down.",
        'network_mode = "public"',
        f"build_timeout_sec = {build_timeout_sec}",
        'os = "linux"',
        "mcp_servers = []",
        "",
        "[environment.env]",
        "",
        "[solution.env]",
        "",
    ]
    return "\n".join(lines)


def _instruction(pr: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        f"# {pr.get('title', pr['id'])}",
        "",
        f"You are working in `{pr['repo']}` at the state just before pull request "
        f"#{pr['number']} was merged.",
        "",
        f"- Pull request: {pr.get('url', pr['id'])}",
        f"- Merged: {pr.get('merged_at', 'unknown')}",
        f"- Branch: `{pr.get('head_ref', 'unknown')}`",
        f"- Size: +{pr.get('additions', 0)} / -{pr.get('deletions', 0)} lines",
        f"- Benchmark split: {pr.get('split', 'unknown')}",
        "",
        "## Goal",
        "",
        "Implement the change the pull request made. The repository is at `/app`.",
        "",
        "Agent traces for work on this repository before this pull request are",
        "available read-only at `/prior-traces`; use them as context.",
        "",
        "The verifier applies the test suite from the merged state and runs it.",
        "Do not modify test files.",
        "",
        "## TODO(task author)",
        "",
        "Replace this section with the issue description. The corpus does not carry",
        "pull request bodies yet, so this skeleton starts from the title only.",
        "",
    ]
    if summary.get("prior_sessions"):
        lines.append(
            f"Prior context: {summary['prior_sessions']} sessions across "
            f"{summary.get('prior_pull_requests', 0)} earlier pull requests."
        )
        lines.append("")
    return "\n".join(lines)


def _dockerfile(base_image: str) -> str:
    return f"""FROM {base_image}

WORKDIR /app

# Repository at the pre-PR state (materialized by the repository tooling).
COPY repo/ /app/

# Prior agent traces for this repository: read-only context for the agent.
COPY prior-traces/ /prior-traces/

# TODO(task author): install the repository toolchain and the test dependencies
# here so the agent and verifier phases run with no network access.
"""


def _solve_sh(pr_id: str) -> str:
    return f"""#!/bin/bash
# Oracle solution for {pr_id}.
# TODO(task author): implement the oracle fix so `harbor run -a oracle` proves
# the task solvable.
set -euo pipefail
echo "tracebench: oracle solution not implemented for {pr_id}" >&2
exit 1
"""


def _test_sh() -> str:
    return """#!/bin/bash
# Golden-suite verifier: overlay the test files from the pull request's merged
# state onto the agent's repository, run them, and write the reward.
set -u
mkdir -p /logs/verifier
if [ ! -d /tests/golden ] || [ -z "$(ls -A /tests/golden)" ]; then
  echo "tracebench: golden tests are not materialized" >&2
  echo 0 > /logs/verifier/reward.txt
  exit 0
fi
cp -a /tests/golden/. /app/
# TODO(task author): run the repository's test command and map the result:
#   echo 1 > /logs/verifier/reward.txt   # pass
#   echo 0 > /logs/verifier/reward.txt   # fail
echo 0 > /logs/verifier/reward.txt
"""
