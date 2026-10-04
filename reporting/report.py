"""Harbor job aggregate reporter.

Reads the ``aggregate.json`` written by ``reporting/collect.py`` and prints
per-task tables, an overall summary, and text charts to stdout.

Usage:

    python reporting/report.py <aggregate.json>

Failures name the bad path and exit non-zero. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BAR_WIDTH = 20


class ReportError(ValueError):
    """A malformed aggregate file. The message names the bad path."""


def load_aggregate(path: Path) -> dict[str, Any]:
    """Read *path* as an aggregate record, failing closed with the path named."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"{path}: cannot read file: {exc.strerror or exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReportError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ReportError(f"{path}: expected a JSON object")
    for field in ("run_id", "tasks", "overall"):
        if field not in data:
            raise ReportError(f"{path}: missing required field {field!r}")
    if not isinstance(data["tasks"], dict):
        raise ReportError(f"{path}: 'tasks' must be an object")
    if not isinstance(data["overall"], dict):
        raise ReportError(f"{path}: 'overall' must be an object")
    return data


def format_value(value: Any) -> str:
    """Format an aggregate number; missing values render as n/a, never zero."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def bar(fraction: float | None, width: int = BAR_WIDTH) -> str:
    """A fixed-width text bar for a 0..1 fraction; n/a when missing."""
    if fraction is None:
        return "[" + "?" * width + "]"
    clamped = max(0.0, min(1.0, fraction))
    filled = round(clamped * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def task_table(tasks: dict[str, Any]) -> list[str]:
    """Per-task metrics table, one row per task."""
    header = (
        f"{'task':<32} {'n':>3} {'reward':>8} {'passed':>8} {'total':>8} "
        f"{'turns':>7} {'tok/turn':>8} {'ttv_s':>8} {'cost_usd':>9}"
    )
    lines = ["Task results", header]
    for name in sorted(tasks):
        entry = tasks[name]
        reward = entry.get("reward", {}).get("mean")
        lines.append(
            f"{name:<32} "
            f"{entry.get('attempts', 0):>3} "
            f"{format_value(reward):>8} "
            f"{format_value(entry.get('passed', {}).get('mean')):>8} "
            f"{format_value(entry.get('total_cases', {}).get('mean')):>8} "
            f"{format_value(entry.get('turns', {}).get('mean')):>7} "
            f"{format_value(entry.get('tokens_per_turn', {}).get('mean')):>8} "
            f"{format_value(entry.get('time_to_verification_seconds', {}).get('mean')):>8} "
            f"{format_value(entry.get('cost_usd', {}).get('mean')):>9}"
        )
    return lines


def summary(aggregate: dict[str, Any]) -> list[str]:
    """Overall summary plus per-task reward extremes."""
    overall = aggregate["overall"]
    tasks = aggregate["tasks"]
    reward = overall.get("reward", {})
    lines = [
        "Summary",
        f"run: {aggregate['run_id']}",
        f"tasks: {overall.get('tasks')}  trials: {overall.get('trials')}",
        f"mean reward: {format_value(reward.get('mean'))} "
        f"(min {format_value(reward.get('min'))}, max {format_value(reward.get('max'))})",
    ]
    exceptions = sum(len(entry.get("exception_info", [])) for entry in tasks.values())
    lines.append(f"trials with exceptions: {exceptions}")
    return lines


def charts(tasks: dict[str, Any]) -> list[str]:
    """Text charts: reward bars and cost bars, one line per task."""
    lines = ["Reward chart (mean reward per task)"]
    for name in sorted(tasks):
        reward = tasks[name].get("reward", {}).get("mean")
        lines.append(f"{name:<32} {bar(reward)} {format_value(reward):>8}")
    lines.append("Cost chart (mean cost_usd per task)")
    costs = [tasks[name].get("cost_usd", {}).get("mean") for name in sorted(tasks)]
    observed = [cost for cost in costs if cost is not None]
    peak = max(observed) if observed else None
    for name in sorted(tasks):
        cost = tasks[name].get("cost_usd", {}).get("mean")
        fraction = (cost / peak) if (cost is not None and peak) else None
        lines.append(f"{name:<32} {bar(fraction)} {format_value(cost):>9}")
    return lines


def render(aggregate: dict[str, Any]) -> str:
    """Render the full report text for an aggregate record."""
    sections = [
        task_table(aggregate["tasks"]),
        summary(aggregate),
        charts(aggregate["tasks"]),
    ]
    return "\n".join("\n".join(section) for section in sections) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Entry point: ``report.py <aggregate.json>``."""
    parser = argparse.ArgumentParser(description="Print tables, summary, and text charts for an aggregate.json.")
    parser.add_argument("aggregate", type=Path, help="aggregate.json written by reporting/collect.py")
    args = parser.parse_args(argv)
    try:
        aggregate = load_aggregate(args.aggregate)
    except ReportError as exc:
        print(f"report: error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(render(aggregate))
    return 0


if __name__ == "__main__":
    sys.exit(main())
