"""Reporter tests: tables, summary, and text charts from the fixed aggregate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTDATA = Path(__file__).parent / "testdata"
REPORT = Path(__file__).parent.parent / "report.py"


def run_report(aggregate: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPORT), str(aggregate)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_report_prints_tables_summary_and_charts() -> None:
    """The reporter prints every section for the fixed expected aggregate."""
    proc = run_report(TESTDATA / "expected_aggregate.json")
    assert proc.returncode == 0, proc.stderr
    output = proc.stdout
    assert "Task results" in output
    assert "tracebench/peasant-pr-0343" in output
    assert "tracebench/peasant-pr-0344" in output
    assert "Summary" in output
    assert "fixture-job" in output
    assert "Reward chart" in output
    assert "Cost chart" in output


def test_report_marks_missing_values() -> None:
    """The oracle task's null tokens render as n/a, never zero."""
    proc = run_report(TESTDATA / "expected_aggregate.json")
    assert proc.returncode == 0, proc.stderr
    oracle_line = next(
        line for line in proc.stdout.splitlines() if "tracebench/peasant-pr-0344" in line and "[" not in line
    )
    assert "n/a" in oracle_line


def test_report_charts_scale_with_reward() -> None:
    """The higher-reward task draws a longer bar than the lower-reward one."""
    proc = run_report(TESTDATA / "expected_aggregate.json")
    assert proc.returncode == 0, proc.stderr
    chart_lines = [
        line for line in proc.stdout.splitlines() if line.startswith("tracebench/") and "[" in line and "]" in line
    ]
    bars = {line.split()[0]: line.split()[1] for line in chart_lines}
    assert bars["tracebench/peasant-pr-0343"].count("#") > bars["tracebench/peasant-pr-0344"].count("#")


def test_report_missing_file_fails_closed(tmp_path: Path) -> None:
    """A missing aggregate path fails, naming the bad path."""
    missing = tmp_path / "no-such-aggregate.json"
    proc = run_report(missing)
    assert proc.returncode != 0
    assert str(missing) in proc.stderr
