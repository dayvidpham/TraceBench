"""Generate Harbor task skeletons from assembled task payloads.

A skeleton is a Harbor task directory:

- ``task.toml`` - task identity, metadata, timeouts, and network policy
- ``instruction.md`` - agent instructions (scaffolded from the pull request)
- ``environment/`` - the pre-PR repository and prior traces, uploaded into the
  container at environment start; no per-task image is built
- ``solution/solve.sh`` - oracle placeholder
- ``tests/test.sh`` - verifier that overlays the golden suite and runs it

The environment references a shared base image from ``task.toml``
(``[environment].docker_image``); Harbor uploads the non-spec files in
``environment/`` into the container workdir after the image is built. The
golden suite lives under ``tests/`` and is copied to ``/tests`` for the
verifier only, so the agent never sees it.

The pre-PR repository and the golden tests come from the task payload's
integration points (``repo/`` and ``tests/``); an empty payload directory
produces a skeleton whose verifier fails closed until the repository tooling
materializes them.
"""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .task import GENERATED_ENTRIES, clear_generated

#: Tool-owned entries inside a generated task directory. ``--force`` clears
#: exactly these.
SKELETON_ENTRIES = ("environment", "tests", "solution", "task.toml", "instruction.md", "task-payload.json")


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


def find_path_pattern(pattern: str) -> str:
    """Translate a doublestar test glob into a ``find -path`` expression.

    ``find`` matches ``*`` across directory separators, so ``**/`` collapses
    to a leading ``*`` and ``**`` to ``*``.
    """
    translated = pattern
    if translated.startswith("**/"):
        translated = "*" + translated[3:]
    return translated.replace("**", "*")


def build_skeleton(
    payload_dir: str | Path,
    dest: str | Path,
    *,
    org: str = "tracebench",
    base_image: str = "tracebench/peasant-base:latest",
    workdir: str = "/workdir",
    task_version: str = "1.0.0",
    agent_timeout_sec: float = 3600.0,
    verifier_timeout_sec: float = 3600.0,
    force: bool = False,
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
    if dest.exists() and any(dest.iterdir()):
        if not force:
            raise ValueError(
                f"destination {dest} is not empty; choose a fresh directory or pass --force"
            )
        clear_generated(dest, SKELETON_ENTRIES)
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
            base_image=base_image,
            workdir=workdir,
            task_version=task_version,
            agent_timeout_sec=agent_timeout_sec,
            verifier_timeout_sec=verifier_timeout_sec,
        )
    )
    (dest / "instruction.md").write_text(_instruction(pr, summary, workdir))
    _copy_tree(payload / "repo", dest / "environment" / "repo")
    _copy_tree(payload / "prior-traces", dest / "environment" / "prior-traces")
    golden_tests = _copy_tree(payload / "tests", dest / "tests" / "golden")
    manifest = payload / "test-manifest.json"
    if manifest.is_file():
        (dest / "tests" / "test-manifest.json").write_bytes(manifest.read_bytes())

    patterns = _test_patterns(payload)
    _write_script(dest / "tests" / "test.sh", _test_sh(patterns))
    _write_script(dest / "solution" / "solve.sh", _solve_sh(pr_id))

    (dest / "task-payload.json").write_text(
        json.dumps({"pr": pr, "summary": summary}, indent=2) + "\n"
    )
    return Skeleton(pr_id=pr_id, task_name=task_name, path=dest, golden_tests=golden_tests)


def _test_patterns(payload: Path) -> list[str]:
    request = payload / "repo-request.json"
    if request.is_file():
        try:
            patterns = json.loads(request.read_text()).get("test_patterns")
        except json.JSONDecodeError:
            patterns = None
        if isinstance(patterns, list) and patterns:
            return [str(pattern) for pattern in patterns]
    from .task import DEFAULT_TEST_PATTERNS

    return list(DEFAULT_TEST_PATTERNS)


def _copy_tree(source: Path, dest: Path) -> int:
    """Copy a payload directory if it exists; return the number of source files."""
    if not source.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        return 0
    count = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        count += 1
    return count


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
    base_image: str,
    workdir: str,
    task_version: str,
    agent_timeout_sec: float,
    verifier_timeout_sec: float,
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
        *(
            [f"target_configuration = {_toml_string(summary['target_configuration']['name'])}"]
            if isinstance(summary.get("target_configuration"), dict)
            and summary["target_configuration"].get("name")
            else []
        ),
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
        "# Reusable base image; task data is uploaded at environment start.",
        f"docker_image = {_toml_string(base_image)}",
        f"workdir = {_toml_string(workdir)}",
        'network_mode = "public"',
        'os = "linux"',
        "mcp_servers = []",
        "",
        "[environment.env]",
        "",
        "[solution.env]",
        "",
    ]
    return "\n".join(lines)


def _instruction(pr: dict[str, Any], summary: dict[str, Any], workdir: str) -> str:
    lines = [
        f"# {pr.get('title', pr['id'])}",
        "",
        f"You are working in `{pr['repo']}` at the state just before pull request "
        f"#{pr['number']} was merged.",
        "",
        f"- Pull request: {pr.get('url', pr['id'])}",
        f"- Merged: {pr.get('merged_at', 'unknown')}",
        f"- Size: +{pr.get('additions', 0)} / -{pr.get('deletions', 0)} lines",
        f"- Benchmark split: {pr.get('split', 'unknown')}",
        "",
        "## Goal",
        "",
        f"Implement the change the pull request made. The repository is at "
        f"`{workdir}/repo`.",
        "",
        f"Agent traces for work on this repository before this pull request are",
        f"available at `{workdir}/prior-traces` as context.",
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


def _solve_sh(pr_id: str) -> str:
    return f"""#!/bin/bash
# Oracle solution for {pr_id}.
# TODO(task author): implement the oracle fix so `harbor run -a oracle` proves
# the task solvable.
set -euo pipefail
echo "tracebench: oracle solution not implemented for {pr_id}" >&2
exit 1
"""


def _test_sh(patterns: list[str]) -> str:
    lines = [
        "#!/bin/bash",
        "# Golden-suite verifier: remove the pre-PR test files the pull request",
        "# deleted or renamed, overlay the merged-state suite, run it, and write",
        "# the reward.",
        "set -u",
        'APP_DIR="${APP_DIR:-/workdir/repo}"',
        'GOLDEN_DIR="${GOLDEN_DIR:-/tests/golden}"',
        'LOG_DIR="${LOG_DIR:-/logs/verifier}"',
        'mkdir -p "$LOG_DIR"',
        'if [ ! -d "$GOLDEN_DIR" ] || [ -z "$(ls -A "$GOLDEN_DIR" 2>/dev/null)" ]; then',
        '  echo "tracebench: golden tests are not materialized" >&2',
        '  echo 0 > "$LOG_DIR/reward.txt"',
        "  exit 0",
        "fi",
        "for pattern in \\",
    ]
    for pattern in patterns:
        lines.append(f'  "{find_path_pattern(pattern)}" \\')
    lines.extend(
        [
            "; do",
            '  find "$APP_DIR" -path "${APP_DIR}/${pattern}" -type f -delete 2>/dev/null || true',
            "done",
            'cp -a "$GOLDEN_DIR/." "$APP_DIR/"',
            "# TODO(task author): run the repository's test command and map the result:",
            '#   echo 1 > "$LOG_DIR/reward.txt"   # pass',
            '#   echo 0 > "$LOG_DIR/reward.txt"   # fail',
            'echo 0 > "$LOG_DIR/reward.txt"',
            "",
        ]
    )
    return "\n".join(lines)
