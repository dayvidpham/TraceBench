"""Generate Harbor task skeletons from assembled task payloads.

A skeleton is a Harbor task directory:

- ``task.toml`` - task identity, metadata, timeouts, and network policy
- ``instruction.md`` - agent instructions (scaffolded from the pull request)
- ``environment/`` - the pre-PR repository and prior traces, uploaded into the
  container at environment start; no per-task image is built
- ``solution/`` - the payload's oracle (``oracle.patch`` + ``solve.sh``) when
  generated, else a ``solve.sh`` placeholder that fails
- ``tests/test.sh`` - runs ``tests/verifier.py``, which removes the pre-PR test
  files, overlays the golden suite, runs the test command, and writes the
  reward and ``test-results.json``

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

from . import verifier
from .golden import MANIFEST_NAME, glob_to_regex
from .task import GENERATED_ENTRIES, clear_generated, payload_test_patterns

#: Tool-owned entries inside a generated task directory. ``--force`` clears
#: exactly these.
SKELETON_ENTRIES = ("environment", "tests", "solution", "task.toml", "instruction.md", "task-payload.json")
#: Container working directory; the task payload lands under it.
DEFAULT_WORKDIR = "/workdir"
#: Maximum pull request body characters rendered into ``instruction.md``.
#: Longer bodies are cut at this cap with a marker stating the rule; the
#: full body stays in the payload's ``pr.json``.
MAX_BODY_CHARS = 4000


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


def task_dir_name(repo: str, number: Any) -> str:
    """Task directory name for a pull request: ``<slug>-pr-<NNNN>``.

    Raises ``ValueError`` when ``number`` is not an integer.
    """
    if isinstance(number, bool) or not isinstance(number, int):
        try:
            number = int(number)
        except (TypeError, ValueError):
            raise ValueError(
                f"pull request number {number!r} for {repo!r} is not an integer; "
                "fix the corpus record's `number` field"
            ) from None
    return f"{task_slug(repo)}-pr-{number:04d}"


def build_skeleton(
    payload_dir: str | Path,
    dest: str | Path,
    *,
    org: str = "tracebench",
    base_image: str = "tracebench/task-runtime:latest",
    workdir: str = DEFAULT_WORKDIR,
    task_version: str = "1.0.0",
    agent_timeout_sec: float = 3600.0,
    verifier_timeout_sec: float = 3600.0,
    test_command: str = verifier.DEFAULT_TEST_COMMAND,
    healthcheck_command: str | None = None,
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
        if not isinstance(summary, dict):
            raise ValueError(
                f"{payload / 'task.json'} must hold a JSON object, got "
                f"{type(summary).__name__}; regenerate the payload with --force"
            )

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

    task_name = f"{org}/{task_dir_name(pr['repo'], pr['number'])}"
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
            healthcheck_command=healthcheck_command,
        )
    )
    (dest / "instruction.md").write_text(_instruction(pr, summary, workdir))
    _copy_tree(payload / "repo", dest / "environment" / "repo")
    _copy_tree(payload / "prior-traces", dest / "environment" / "prior-traces")
    golden_tests = _copy_tree(payload / "tests", dest / "tests" / "golden", exclude={MANIFEST_NAME})
    golden_manifest = payload / "tests" / MANIFEST_NAME
    if golden_manifest.is_file():
        # Verifier-only record of the extracted paths; never part of the overlay.
        (dest / "tests" / MANIFEST_NAME).write_bytes(golden_manifest.read_bytes())
    manifest = payload / "test-manifest.json"
    if manifest.is_file():
        (dest / "tests" / "test-manifest.json").write_bytes(manifest.read_bytes())

    request = payload / "repo-request.json"
    if request.is_file():
        (dest / "tests" / "repo-request.json").write_bytes(request.read_bytes())
    patterns = payload_test_patterns(payload)
    (dest / "tests" / verifier.CONFIG_NAME).write_text(
        json.dumps(
            {
                "pr": pr_id,
                "test_command": test_command,
                "remove_patterns": patterns,
                # Compiled by the canonical doublestar matcher; the verifier only
                # applies them, so deletion and golden extraction never disagree.
                "remove_regexes": [glob_to_regex(pattern).pattern for pattern in patterns],
            },
            indent=2,
        )
        + "\n"
    )
    (dest / "tests" / "verifier.py").write_bytes(Path(verifier.__file__).read_bytes())
    # Leave headroom under Harbor's verifier timeout so a hung test run is
    # recorded as killed instead of losing the report.
    _write_script(dest / "tests" / "test.sh", _test_sh(max(verifier_timeout_sec - 60.0, verifier_timeout_sec * 0.9)))
    if (payload / "solution" / "solve.sh").is_file():
        _copy_tree(payload / "solution", dest / "solution")
        (dest / "solution" / "solve.sh").chmod(0o755)
    else:
        _write_script(dest / "solution" / "solve.sh", _solve_sh(pr_id))

    (dest / "task-payload.json").write_text(
        json.dumps({"pr": pr, "summary": summary}, indent=2) + "\n"
    )
    return Skeleton(pr_id=pr_id, task_name=task_name, path=dest, golden_tests=golden_tests)


def _copy_tree(source: Path, dest: Path, exclude: frozenset[str] | set[str] = frozenset()) -> int:
    """Copy a payload directory if it exists; return the number of copied files.

    ``exclude`` names top-level entries of ``source`` that are not copied.
    """
    if not source.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        return 0
    count = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if relative.parts[0] in exclude:
            continue
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
    healthcheck_command: str | None = None,
) -> str:
    title = pr.get("title") or ""
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
        f"pr_url = {_toml_string(pr.get('url') or '')}",
        f"pr_number = {pr['number']}",
        f"split = {_toml_string(pr.get('split') or '')}",
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
        *(
            [
                "",
                "[environment.healthcheck]",
                "# Warm the pre-PR dependency and build caches while the environment",
                "# network is still up, so the offline agent and verifier can build.",
                "# Doubles as a readiness check: the pre-PR tree must build first.",
                f"command = {_toml_string(healthcheck_command)}",
                "timeout_sec = 1800.0",
                "retries = 2",
            ]
            if healthcheck_command
            else []
        ),
        "",
        "[environment.env]",
        "",
        "[solution.env]",
        "",
    ]
    return "\n".join(lines)


def _instruction(pr: dict[str, Any], summary: dict[str, Any], workdir: str) -> str:
    lines = [
        f"# {pr.get('title') or pr['id']}",
        "",
        f"You are working in `{pr['repo']}` at the state just before pull request "
        f"#{pr['number']} was merged.",
        "",
        f"- Pull request: {pr.get('url') or pr['id']}",
        f"- Merged: {pr.get('merged_at') or 'unknown'}",
        f"- Size: +{pr.get('additions') or 0} / -{pr.get('deletions') or 0} lines",
        f"- Benchmark split: {pr.get('split') or 'unknown'}",
        "",
        "## Goal",
        "",
    ]
    raw_body = pr.get("body")
    if raw_body is None:
        body = ""
    elif not isinstance(raw_body, str):
        raise ValueError(
            f"task payload pr.json field `body` must be a string, got "
            f"{type(raw_body).__name__}; regenerate the payload"
        )
    else:
        body = raw_body.strip()
    if body:
        lines.append(_truncate_body(body))
        lines.append("")
        lines.append(f"The repository is at `{workdir}/repo`.")
        lines.append("")
    else:
        lines.extend(
            [
                f"Implement the change the pull request made. The repository is at "
                f"`{workdir}/repo`.",
                "",
            ]
        )
    lines.extend(
        [
            f"Agent traces for work on this repository before this pull request are",
            f"available at `{workdir}/prior-traces` as context.",
            "",
            "The verifier applies the test suite from the merged state and runs it.",
            "Do not modify test files.",
            "",
        ]
    )
    if not body:
        lines.extend(
            [
                "## TODO(task author)",
                "",
                "Replace this section with the issue description. This pull request",
                "carries no body, so this skeleton starts from the title only.",
                "",
            ]
        )
    if summary.get("prior_sessions"):
        lines.append(
            f"Prior context: {summary['prior_sessions']} sessions across "
            f"{summary.get('prior_pull_requests', 0)} earlier pull requests."
        )
        lines.append("")
    return "\n".join(lines)


def _truncate_body(body: str) -> str:
    """Render a pull request body for ``instruction.md``.

    Bodies longer than :data:`MAX_BODY_CHARS` are cut at the cap with a
    marker stating the rule.
    """
    if len(body) <= MAX_BODY_CHARS:
        return body
    return (
        body[:MAX_BODY_CHARS].rstrip()
        + f"\n\n[truncated: pull request body exceeded {MAX_BODY_CHARS} characters]"
    )


def _solve_sh(pr_id: str) -> str:
    return f"""#!/bin/bash
# Oracle solution for {pr_id}.
# TODO(task author): implement the oracle fix so `harbor run -a oracle` proves
# the task solvable.
set -euo pipefail
echo "tracebench: oracle solution not implemented for {pr_id}" >&2
exit 1
"""


def _test_sh(timeout_sec: float) -> str:
    return f"""#!/bin/bash
# Golden-suite verifier: remove the pre-PR test files, overlay the merged-state
# suite, run the repository test command, and write the reward and report.
# The grading logic lives in verifier.py next to this script.
set -u
TESTS_DIR="${{TESTS_DIR:-$(cd "$(dirname "$0")" && pwd)}}"
APP_DIR="${{APP_DIR:-/workdir/repo}}"
GOLDEN_DIR="${{GOLDEN_DIR:-$TESTS_DIR/golden}}"
LOG_DIR="${{LOG_DIR:-/logs/verifier}}"
mkdir -p "$LOG_DIR"
if ! python3 "$TESTS_DIR/verifier.py" run \\
    --tests-dir "$TESTS_DIR" --app-dir "$APP_DIR" --golden-dir "$GOLDEN_DIR" \\
    --log-dir "$LOG_DIR" --timeout-sec {timeout_sec}; then
  echo "tracebench: verifier.py crashed; reward 0 (see the traceback above)" >&2
  echo 0 > "$LOG_DIR/reward.txt"
  if [ ! -f "$LOG_DIR/test-results.json" ]; then
    echo '{{"schema_version": {verifier.REPORT_SCHEMA_VERSION}, "reward": 0, "fail_closed_reasons": ["verifier.py crashed before writing a report"]}}' \\
      > "$LOG_DIR/test-results.json"
  fi
fi
exit 0
"""
