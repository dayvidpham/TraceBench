"""Collector tests: golden fixture, errored trials, and fail-closed inputs.

The tests run the production entry point (``python reporting/collect.py``)
against checked-in fixture job directories under ``testdata/``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TESTDATA = Path(__file__).parent / "testdata"
COLLECT = Path(__file__).parent.parent / "collect.py"


def run_collect(job_dir: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(COLLECT), str(job_dir), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_fixture_matches_expected_aggregate(tmp_path: Path) -> None:
    """One model-style trial plus one oracle trial produce the fixed aggregate."""
    out = tmp_path / "aggregate.json"
    proc = run_collect(TESTDATA / "job", out)
    assert proc.returncode == 0, proc.stderr
    expected = json.loads((TESTDATA / "expected_aggregate.json").read_text(encoding="utf-8"))
    actual = json.loads(out.read_text(encoding="utf-8"))
    assert actual == expected


def test_nulls_stay_null(tmp_path: Path) -> None:
    """The oracle trial keeps null tokens and turns instead of zeros."""
    out = tmp_path / "aggregate.json"
    assert run_collect(TESTDATA / "job", out).returncode == 0
    oracle = json.loads(out.read_text(encoding="utf-8"))["tasks"]["tracebench/peasant-pr-0344"]
    for metric in ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd", "tokens_per_turn", "turns"):
        assert oracle[metric] == {"mean": None, "min": None, "max": None}
        assert 0 not in oracle[metric].values()


def test_errored_trial_keeps_nulls_and_records_exception(tmp_path: Path) -> None:
    """A trial that fails before verification records nulls plus its exception."""
    out = tmp_path / "aggregate.json"
    proc = run_collect(TESTDATA / "job-errored", out)
    assert proc.returncode == 0, proc.stderr
    task = json.loads(out.read_text(encoding="utf-8"))["tasks"]["tracebench/peasant-pr-0343"]
    assert task["attempts"] == 1
    assert task["reward"] == {"mean": None, "min": None, "max": None}
    assert task["exception_info"] == [
        {
            "trial": "peasant-pr-0343__failed",
            "exception_type": "HealthcheckError",
            "exception_message": "Healthcheck failed after 2 consecutive retries",
        }
    ]


def test_nested_trials_subdirectory_is_accepted(tmp_path: Path) -> None:
    """Reorganized evidence trees with trials under trials/ collect the same way."""
    out = tmp_path / "aggregate.json"
    proc = run_collect(TESTDATA / "job-nested", out)
    assert proc.returncode == 0, proc.stderr
    aggregate = json.loads(out.read_text(encoding="utf-8"))
    assert aggregate["run_id"] == "nested-job"
    task = aggregate["tasks"]["tracebench/peasant-pr-0343"]
    assert task["attempts"] == 1
    assert task["reward"] == {"mean": 0.9, "min": 0.9, "max": 0.9}
    assert task["turns"] == {"mean": 5.0, "min": 5, "max": 5}


def test_missing_result_json_fails_closed(tmp_path: Path) -> None:
    """A job with no trial result files fails, naming the job directory."""
    job = TESTDATA / "malformed-missing-result" / "job"
    proc = run_collect(job, tmp_path / "aggregate.json")
    assert proc.returncode != 0
    assert str(job) in proc.stderr


def test_invalid_result_json_fails_closed(tmp_path: Path) -> None:
    """An unparsable result.json fails, naming the bad file."""
    bad = TESTDATA / "malformed-result-json" / "job" / "peasant-pr-0343__bad" / "result.json"
    proc = run_collect(TESTDATA / "malformed-result-json" / "job", tmp_path / "aggregate.json")
    assert proc.returncode != 0
    assert str(bad) in proc.stderr


def test_invalid_test_results_fails_closed(tmp_path: Path) -> None:
    """A malformed test-results.json fails, naming the bad file."""
    bad = TESTDATA / "malformed-test-results" / "job" / "peasant-pr-0343__bad" / "verifier" / "test-results.json"
    proc = run_collect(TESTDATA / "malformed-test-results" / "job", tmp_path / "aggregate.json")
    assert proc.returncode != 0
    assert str(bad) in proc.stderr


def test_invalid_trajectory_fails_closed(tmp_path: Path) -> None:
    """A malformed trajectory.json fails, naming the bad file."""
    bad = TESTDATA / "malformed-trajectory" / "job" / "peasant-pr-0343__bad" / "agent" / "trajectory.json"
    proc = run_collect(TESTDATA / "malformed-trajectory" / "job", tmp_path / "aggregate.json")
    assert proc.returncode != 0
    assert str(bad) in proc.stderr
