"""Harbor job aggregate collector.

Reads a Harbor job directory (one subdirectory per trial) and writes
``aggregate.json``: per-task means across attempts plus an overall summary.

Each trial directory holds Harbor's ``result.json`` (trial identity,
``agent_result`` token and cost counts, ``agent_execution`` and ``verifier``
timestamps, ``exception_info``), the verifier's ``test-results.json``
(``passed``, ``failed``, ``skipped``, ``missing``, ``total_cases``,
``rejected_cases``), and, for model agents, ``agent/trajectory.json`` with
``final_metrics.total_steps``. Oracle trials have no trajectory, so their
turn counts stay null. Trial directories are direct children of the job
directory; a ``trials/`` subdirectory is accepted as a fallback for
reorganized evidence trees.

Derived per-trial values:

- ``reward`` is ``passed / total_cases``.
- ``time_to_verification_seconds`` is ``verifier.started_at`` minus
  ``agent_execution.started_at``, in seconds.
- ``turns`` is ``total_steps - 1``.
- ``tokens_per_turn`` is ``n_output_tokens / turns``.

A missing value stays null; this module never writes zero for one. A trial
that fails before verification (no ``test-results.json``) keeps its
verifier-derived values null and records its ``exception_info``.

Usage:

    python reporting/collect.py <job-dir> <out.json>

The command fails closed: any malformed trial file aborts the run with an
error that names the bad path. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Version of the ``aggregate.json`` layout this module writes.
AGGREGATE_SCHEMA_VERSION = 1

#: Per-trial numeric metrics aggregated with mean/min/max across attempts.
STAT_METRICS = (
    "reward",
    "passed",
    "failed",
    "skipped",
    "missing",
    "total_cases",
    "rejected_cases",
    "n_input_tokens",
    "n_cache_tokens",
    "n_output_tokens",
    "cost_usd",
    "tokens_per_turn",
    "turns",
    "time_to_verification_seconds",
)

RESULT_FILE = "result.json"
TEST_RESULTS_FILE = Path("verifier") / "test-results.json"
TRAJECTORY_FILE = Path("agent") / "trajectory.json"
JOB_CONFIG_FILE = "config.json"


class CollectError(ValueError):
    """A malformed job or trial file. The message names the bad path."""


def load_json_object(path: Path) -> dict[str, Any]:
    """Read *path* as a JSON object, failing closed with the path named."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CollectError(f"{path}: cannot read file: {exc.strerror or exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CollectError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CollectError(f"{path}: expected a JSON object")
    return data


def parse_timestamp(value: Any, path: Path, field: str) -> datetime:
    """Parse an ISO 8601 timestamp, failing closed with the path named."""
    if not isinstance(value, str):
        raise CollectError(f"{path}: {field} must be a string timestamp")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CollectError(f"{path}: {field} is not a valid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def optional_number(value: Any, path: Path, field: str) -> float | int | None:
    """Return a numeric field or None, failing closed on a wrong type."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectError(f"{path}: {field} must be a number or null")
    return value


def required_count(data: dict[str, Any], path: Path, field: str) -> int:
    """Return an integer count field, failing closed with the path named."""
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CollectError(f"{path}: {field} must be an integer")
    return value


def collect_trial(trial_dir: Path) -> dict[str, Any]:
    """Collect one trial's metrics. Missing values stay null, never zero."""
    result_path = trial_dir / RESULT_FILE
    result = load_json_object(result_path)

    task_name = result.get("task_name")
    if not isinstance(task_name, str) or not task_name:
        fallback = trial_dir.name.split("__")[0]
        if not fallback:
            raise CollectError(f"{result_path}: missing task_name")
        task_name = fallback
    trial_name = result.get("trial_name")
    if not isinstance(trial_name, str) or not trial_name:
        trial_name = trial_dir.name

    agent_result = result.get("agent_result") or {}
    if not isinstance(agent_result, dict):
        raise CollectError(f"{result_path}: agent_result must be an object or null")
    tokens = {
        metric: optional_number(agent_result.get(metric), result_path, f"agent_result.{metric}")
        for metric in ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")
    }

    agent_execution = result.get("agent_execution") or {}
    if not isinstance(agent_execution, dict):
        raise CollectError(f"{result_path}: agent_execution must be an object or null")
    verifier_block = result.get("verifier") or {}
    if not isinstance(verifier_block, dict):
        raise CollectError(f"{result_path}: verifier must be an object or null")

    time_to_verification: float | None = None
    if agent_execution.get("started_at") is not None and verifier_block.get("started_at") is not None:
        agent_started = parse_timestamp(agent_execution["started_at"], result_path, "agent_execution.started_at")
        verifier_started = parse_timestamp(verifier_block["started_at"], result_path, "verifier.started_at")
        time_to_verification = (verifier_started - agent_started).total_seconds()

    exception = result.get("exception_info")
    if exception is not None and not isinstance(exception, dict):
        raise CollectError(f"{result_path}: exception_info must be an object or null")

    trial: dict[str, Any] = {
        "trial": trial_name,
        "task": task_name,
        "reward": None,
        "passed": None,
        "failed": None,
        "skipped": None,
        "missing": None,
        "total_cases": None,
        "rejected_cases": None,
        "n_input_tokens": tokens["n_input_tokens"],
        "n_cache_tokens": tokens["n_cache_tokens"],
        "n_output_tokens": tokens["n_output_tokens"],
        "cost_usd": tokens["cost_usd"],
        "tokens_per_turn": None,
        "turns": None,
        "time_to_verification_seconds": time_to_verification,
        "exception": None,
    }

    test_results_path = trial_dir / TEST_RESULTS_FILE
    if test_results_path.exists():
        report = load_json_object(test_results_path)
        trial["passed"] = required_count(report, test_results_path, "passed")
        trial["failed"] = required_count(report, test_results_path, "failed")
        trial["skipped"] = required_count(report, test_results_path, "skipped")
        trial["missing"] = required_count(report, test_results_path, "missing")
        trial["total_cases"] = required_count(report, test_results_path, "total_cases")
        trial["rejected_cases"] = required_count(report, test_results_path, "rejected_cases")
        if trial["total_cases"] > 0:
            trial["reward"] = trial["passed"] / trial["total_cases"]

    trajectory_path = trial_dir / TRAJECTORY_FILE
    if trajectory_path.exists():
        trajectory = load_json_object(trajectory_path)
        metrics = trajectory.get("final_metrics") or {}
        if not isinstance(metrics, dict):
            raise CollectError(f"{trajectory_path}: final_metrics must be an object or null")
        total_steps = metrics.get("total_steps")
        if total_steps is not None:
            if isinstance(total_steps, bool) or not isinstance(total_steps, int):
                raise CollectError(f"{trajectory_path}: final_metrics.total_steps must be an integer")
            if total_steps < 1:
                raise CollectError(f"{trajectory_path}: final_metrics.total_steps must be at least 1")
            trial["turns"] = total_steps - 1

    output_tokens = trial["n_output_tokens"]
    turns = trial["turns"]
    if output_tokens is not None and turns is not None and turns > 0:
        trial["tokens_per_turn"] = output_tokens / turns

    if exception is not None:
        trial["exception"] = {
            "trial": trial_name,
            "exception_type": exception.get("exception_type"),
            "exception_message": exception.get("exception_message"),
        }

    return trial


def summarize(values: list[float | int | None]) -> dict[str, float | None]:
    """Mean/min/max over the non-null values; all null when there are none."""
    observed = [value for value in values if value is not None]
    if not observed:
        return {"mean": None, "min": None, "max": None}
    return {
        "mean": sum(observed) / len(observed),
        "min": min(observed),
        "max": max(observed),
    }


def find_trial_dirs(job_dir: Path) -> list[Path]:
    """Trial directories hold ``result.json``.

    Harbor writes one directory per trial directly under the job directory;
    reorganized evidence trees nest them under ``trials/`` instead. Direct
    children win; the ``trials/`` subdirectory is the fallback.
    """
    direct = sorted(entry for entry in job_dir.iterdir() if entry.is_dir() and (entry / RESULT_FILE).exists())
    if direct:
        return direct
    nested = job_dir / "trials"
    if nested.is_dir():
        return sorted(entry for entry in nested.iterdir() if entry.is_dir() and (entry / RESULT_FILE).exists())
    return []


def collect_job(job_dir: Path) -> dict[str, Any]:
    """Collect every trial under *job_dir* into the aggregate record."""
    if not job_dir.is_dir():
        raise CollectError(f"{job_dir}: job directory does not exist")
    trial_dirs = find_trial_dirs(job_dir)
    if not trial_dirs:
        raise CollectError(f"{job_dir}: no trial directories with {RESULT_FILE}")

    config_path = job_dir / JOB_CONFIG_FILE
    run_id = job_dir.name
    if config_path.exists():
        config = load_json_object(config_path)
        if isinstance(config.get("job_name"), str) and config["job_name"]:
            run_id = config["job_name"]

    trials = [collect_trial(trial_dir) for trial_dir in trial_dirs]

    by_task: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        by_task.setdefault(trial["task"], []).append(trial)

    tasks: dict[str, Any] = {}
    for task_name in sorted(by_task):
        task_trials = by_task[task_name]
        entry: dict[str, Any] = {"attempts": len(task_trials)}
        for metric in STAT_METRICS:
            entry[metric] = summarize([trial[metric] for trial in task_trials])
        entry["exception_info"] = [trial["exception"] for trial in task_trials if trial["exception"] is not None]
        tasks[task_name] = entry

    return {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "run_id": run_id,
        "tasks": tasks,
        "overall": {
            "tasks": len(tasks),
            "trials": len(trials),
            "reward": summarize([trial["reward"] for trial in trials]),
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point: ``collect.py <job-dir> <out.json>``."""
    parser = argparse.ArgumentParser(description="Aggregate one Harbor job directory into aggregate.json.")
    parser.add_argument("job_dir", type=Path, help="Harbor job directory with one subdirectory per trial")
    parser.add_argument("out", type=Path, help="destination path for aggregate.json")
    args = parser.parse_args(argv)
    try:
        aggregate = collect_job(args.job_dir)
    except CollectError as exc:
        print(f"collect: error: {exc}", file=sys.stderr)
        return 1
    try:
        args.out.write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"collect: error: {args.out}: cannot write file: {exc.strerror or exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
