#!/usr/bin/env python3
"""Plot per-PR test outcomes from Harbor completed-run exports.

The input folders (default: out/david, out/frank, out/nick) are scanned for
completed Harbor runs, both as extracted directory trees and as .zip archives.
Each scored attempt is read from <job>/<trial>/verifier/test-results.json, and
the sibling <job>/<trial>/result.json supplies the attempt timestamp. Trials
that have a result.json but no verifier output are reported as errored and are
excluded from the charts (they stay in the CSV and in the summary table).

Outputs (default under out/plots/):
  pr_test_share.png       stacked passed/failed share per PR, mean over attempts
  pr_attempt_success.png  success rate per PR across attempt order, one colour per PR
  pr_trials.csv           merged per-attempt table (scored and errored)

Usage:
  uv run python scripts/plot_run_tests.py
  uv run python scripts/plot_run_tests.py --input out/david --show
"""

import argparse
import csv
import json
import re
import statistics
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_INPUTS = (
    REPO_ROOT / "out" / "david",
    REPO_ROOT / "out" / "frank",
    REPO_ROOT / "out" / "nick",
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "out" / "plots"

PASS_COLOR = "#3f9b63"
FAIL_COLOR = "#d34f4f"

SCORED_RE = re.compile(r"^(?P<job>[^/]+)/(?P<trial>[^/]+)/verifier/test-results\.json$")
TRIAL_RESULT_RE = re.compile(r"^(?P<job>[^/]+)/(?P<trial>[^/]+)/result\.json$")
TRIAL_DIR_RE = re.compile(r"^[^/]+__[A-Za-z0-9]+$")


@dataclass(frozen=True)
class Attempt:
    """A verifier-scored Harbor attempt."""

    pr: str
    pr_number: int | None
    job: str
    trial: str
    source: str
    container: str
    passed: int
    failed: int
    skipped: int
    missing: int
    total_cases: int
    reward: float
    success_rate: float
    timestamp: float | None

    @property
    def label(self) -> str:
        return _display_label(self.pr, self.pr_number, self.trial)

    @property
    def sort_key(self) -> tuple:
        return (self.timestamp is None, self.timestamp or 0.0, self.job, self.trial)


@dataclass(frozen=True)
class ErroredAttempt:
    """A Harbor attempt whose verifier produced no test results."""

    pr: str
    pr_number: int | None
    job: str
    trial: str
    source: str
    container: str
    timestamp: float | None

    @property
    def label(self) -> str:
        return _display_label(self.pr, self.pr_number, self.trial)

    @property
    def sort_key(self) -> tuple:
        return (self.timestamp is None, self.timestamp or 0.0, self.job, self.trial)


@dataclass
class PrSeries:
    """All attempts, scored and errored, that belong to one PR."""

    label: str
    number: int | None
    attempts: list  # list[Attempt], chronological
    errored: list  # list[ErroredAttempt], chronological

    @property
    def all_attempts(self) -> list:
        return sorted([*self.attempts, *self.errored], key=lambda item: item.sort_key)


def _display_label(pr: str, pr_number: int | None, fallback: str) -> str:
    if pr_number is not None:
        return f"PR #{pr_number}"
    return pr or fallback


def _parse_timestamp(value) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _trial_timestamp(trial_result: dict | None) -> float | None:
    if not isinstance(trial_result, dict):
        return None
    verifier = trial_result.get("verifier")
    if isinstance(verifier, dict):
        for stamp in ("started_at", "finished_at"):
            parsed = _parse_timestamp(verifier.get(stamp))
            if parsed is not None:
                return parsed
    for stamp in ("started_at", "finished_at"):
        parsed = _parse_timestamp(trial_result.get(stamp))
        if parsed is not None:
            return parsed
    return None


def _parse_pr(raw_pr, task: str) -> tuple[str, int | None]:
    pr = str(raw_pr).strip() if raw_pr else ""
    number = None
    match = re.search(r"#(\d+)", pr)
    if match:
        number = int(match.group(1))
    if number is None and task:
        match = re.search(r"(\d+)$", task)
        if match:
            number = int(match.group(1))
    if not pr and number is not None:
        pr = f"PR #{number}"
    return pr, number


def _canonical_json(raw: bytes, label: str, warnings: list) -> dict | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        warnings.append(f"skipping {label}: {exc}")
        return None
    if not isinstance(data, dict):
        warnings.append(f"skipping {label}: expected a JSON object")
        return None
    return data


def _as_int(value) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _build_attempt(raw_results, trial_result, job, trial, source, container) -> Attempt:
    task = trial.split("__", 1)[0]
    pr, number = _parse_pr(raw_results.get("pr"), task)
    passed = _as_int(raw_results.get("passed"))
    failed = _as_int(raw_results.get("failed"))
    skipped = _as_int(raw_results.get("skipped"))
    missing = _as_int(raw_results.get("missing"))
    total = _as_int(raw_results.get("total_cases"))
    if total <= 0:
        total = passed + failed + skipped + missing
    success_rate = passed / total if total > 0 else 0.0
    raw_reward = raw_results.get("reward")
    reward = float(raw_reward) if isinstance(raw_reward, (int, float)) and not isinstance(raw_reward, bool) else success_rate
    return Attempt(
        pr=pr,
        pr_number=number,
        job=job,
        trial=trial,
        source=source,
        container=container,
        passed=passed,
        failed=failed,
        skipped=skipped,
        missing=missing,
        total_cases=total,
        reward=reward,
        success_rate=success_rate,
        timestamp=_trial_timestamp(trial_result),
    )


def _build_errored(trial_result, job, trial, source, container) -> ErroredAttempt:
    task = trial.split("__", 1)[0]
    pr, number = _parse_pr(None, task)
    return ErroredAttempt(
        pr=pr,
        pr_number=number,
        job=job,
        trial=trial,
        source=source,
        container=container,
        timestamp=_trial_timestamp(trial_result),
    )


class RunScanner:
    """Collect scored and errored trials from run folders and .zip archives."""

    def __init__(self) -> None:
        self.attempts: list = []  # list[Attempt]
        self.errored: list = []  # list[ErroredAttempt]
        self.warnings: list = []
        self.duplicates = 0
        self._seen: set = set()

    def scan(self, root: Path, source: str) -> None:
        if not root.exists():
            self.warnings.append(f"input folder not found: {root}")
            return
        if root.is_file():
            if root.suffix == ".zip":
                self._scan_zip(root, source)
            else:
                self.warnings.append(f"input is not a folder or .zip: {root}")
            return
        self._scan_tree(root, source)
        for zip_path in sorted(root.rglob("*.zip")):
            self._scan_zip(zip_path, source)

    def _register(self, job: str, trial: str) -> bool:
        key = (job, trial)
        if key in self._seen:
            self.duplicates += 1
            return False
        self._seen.add(key)
        return True

    def _read_bytes(self, path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except OSError as exc:
            self.warnings.append(f"unreadable {path}: {exc}")
            return None

    def _scan_tree(self, root: Path, source: str) -> None:
        for results_path in sorted(root.rglob("verifier/test-results.json")):
            trial_dir = results_path.parent.parent
            job = trial_dir.parent.name
            trial = trial_dir.name
            raw = self._read_bytes(results_path)
            if raw is None:
                continue
            raw_results = _canonical_json(raw, str(results_path), self.warnings)
            if raw_results is None:
                continue
            trial_result = None
            result_path = trial_dir / "result.json"
            if result_path.is_file():
                raw_trial = self._read_bytes(result_path)
                if raw_trial is not None:
                    trial_result = _canonical_json(raw_trial, str(result_path), self.warnings)
            if not self._register(job, trial):
                continue
            self.attempts.append(
                _build_attempt(raw_results, trial_result, job, trial, source, str(trial_dir))
            )
        for result_path in sorted(root.rglob("result.json")):
            parent = result_path.parent
            if not TRIAL_DIR_RE.fullmatch(parent.name):
                continue
            if (parent / "verifier" / "test-results.json").is_file():
                continue
            raw_trial = self._read_bytes(result_path)
            if raw_trial is None:
                continue
            trial_result = _canonical_json(raw_trial, str(result_path), self.warnings)
            job = parent.parent.name
            trial = parent.name
            if not self._register(job, trial):
                continue
            self.errored.append(_build_errored(trial_result, job, trial, source, str(parent)))

    def _scan_zip(self, path: Path, source: str) -> None:
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            self.warnings.append(f"skipping archive {path}: {exc}")
            return
        with archive:
            names = set(archive.namelist())
            for name in sorted(names):
                match = SCORED_RE.match(name)
                if match:
                    job = match.group("job")
                    trial = match.group("trial")
                    raw_results = _canonical_json(archive.read(name), f"{path}:{name}", self.warnings)
                    if raw_results is None:
                        continue
                    trial_result = None
                    result_name = f"{job}/{trial}/result.json"
                    if result_name in names:
                        trial_result = _canonical_json(
                            archive.read(result_name), f"{path}:{result_name}", self.warnings
                        )
                    if not self._register(job, trial):
                        continue
                    self.attempts.append(
                        _build_attempt(raw_results, trial_result, job, trial, source, f"{path}::{name}")
                    )
                    continue
                match = TRIAL_RESULT_RE.match(name)
                if not match:
                    continue
                job = match.group("job")
                trial = match.group("trial")
                if not TRIAL_DIR_RE.fullmatch(trial):
                    continue
                if f"{job}/{trial}/verifier/test-results.json" in names:
                    continue
                trial_result = _canonical_json(archive.read(name), f"{path}:{name}", self.warnings)
                if not self._register(job, trial):
                    continue
                self.errored.append(
                    _build_errored(trial_result, job, trial, source, f"{path}::{name}")
                )


def group_series(attempts: list, errored: list) -> list:
    """Group attempts by PR, sorted by PR number; attempts sorted chronologically."""
    grouped: dict = {}
    for item in [*attempts, *errored]:
        grouped.setdefault(item.label, []).append(item)
    series = []
    for label, items in grouped.items():
        number = items[0].pr_number
        series.append(
            PrSeries(
                label=label,
                number=number,
                attempts=sorted([i for i in items if isinstance(i, Attempt)], key=lambda a: a.sort_key),
                errored=sorted([i for i in items if isinstance(i, ErroredAttempt)], key=lambda a: a.sort_key),
            )
        )
    series.sort(key=lambda s: (s.number is None, s.number if s.number is not None else 0, s.label))
    return series


def _load_pyplot(show: bool):
    try:
        import matplotlib
    except ImportError:
        raise SystemExit(
            "error: matplotlib is not installed; install it with `uv add --dev matplotlib`"
        )
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return matplotlib, plt


def _pr_colors(mpl, count: int) -> list:
    if count <= 10:
        cmap = mpl.colormaps["tab10"]
        return [cmap(i) for i in range(count)]
    if count <= 20:
        cmap = mpl.colormaps["tab20"]
        return [cmap(i) for i in range(count)]
    cmap = mpl.colormaps["hsv"]
    return [cmap(i / count) for i in range(count)]


def _fmt_count(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def _footer(fig, text: str) -> None:
    fig.text(0.01, -0.015, text, fontsize=7.5, color="dimgray", ha="left", va="top")


def plot_stacked_share(mpl, plt, series: list, out_path: Path, dpi: int) -> None:
    """Stacked passed/failed share per PR, mean over scored attempts."""
    pass_means = [statistics.fmean(a.passed for a in s.attempts) for s in series]
    fail_means = [statistics.fmean(a.failed for a in s.attempts) for s in series]
    shares = [
        100.0 * p / (p + f) if p + f > 0 else 0.0 for p, f in zip(pass_means, fail_means)
    ]
    positions = list(range(len(series)))
    width = min(18.0, max(9.0, 1.15 * len(series) + 3.0))
    rotated = len(series) > 14
    fig, ax = plt.subplots(figsize=(width, 6.2))
    ax.bar(positions, shares, width=0.62, color=PASS_COLOR, edgecolor="white", linewidth=0.8, label="passed")
    ax.bar(
        positions,
        [100.0 - s for s in shares],
        width=0.62,
        bottom=shares,
        color=FAIL_COLOR,
        edgecolor="white",
        linewidth=0.8,
        label="failed",
    )
    for pos, share, mean_pass, mean_fail in zip(positions, shares, pass_means, fail_means):
        above = []
        if share >= 12:
            ax.text(pos, share / 2, _fmt_count(mean_pass), ha="center", va="center", fontsize=9, color="white", fontweight="bold")
        else:
            above.append(f"{_fmt_count(mean_pass)} pass")
        if 100.0 - share >= 12:
            ax.text(pos, share + (100.0 - share) / 2, _fmt_count(mean_fail), ha="center", va="center", fontsize=9, color="white", fontweight="bold")
        else:
            above.append(f"{_fmt_count(mean_fail)} fail")
        if above:
            ax.text(pos, 101.5, " / ".join(above), ha="center", va="bottom", fontsize=7.5, color="black")
    labels = []
    for s in series:
        runs = len(s.attempts)
        suffix = f"{runs} run{'s' if runs != 1 else ''}"
        skipped = statistics.fmean(a.skipped + a.missing for a in s.attempts) if s.attempts else 0.0
        if skipped >= 0.5:
            suffix += f" \u00b7 {_fmt_count(skipped)} skipped"
        labels.append(f"{s.label}\n({suffix})")
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontsize=8)
    if rotated:
        for tick in ax.get_xticklabels():
            tick.set_rotation(30)
            tick.set_ha("right")
    ax.set_ylim(0, 112)
    ax.set_yticks(range(0, 101, 10))
    ax.set_ylabel("share of scored tests (%)")
    ax.set_title("Test outcomes per PR \u2014 stacked share of scored tests", loc="left", fontsize=12.5, pad=10)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncols=2, frameon=False, fontsize=9)
    errored_total = sum(len(s.errored) for s in series)
    note = (
        "passed vs failed shares, averaged over scored attempts; skipped/missing tests are excluded"
    )
    if errored_total:
        note += f"; {errored_total} errored attempt(s) without verifier results are excluded."
    else:
        note += "."
    ax.set_xlabel(note, fontsize=7.5, color="dimgray", loc="left", labelpad=10)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def plot_attempt_success(mpl, plt, series: list, out_path: Path, include_errored: bool, dpi: int) -> None:
    """Success rate per PR across attempt order, one colour per PR."""
    colors = _pr_colors(mpl, len(series))
    fig, ax = plt.subplots(figsize=(11.5, 6.5))
    max_attempts = 1
    for pr_series, color in zip(series, colors):
        attempts = pr_series.all_attempts
        scored_x, scored_y, errored_x = [], [], []
        for index, attempt in enumerate(attempts, start=1):
            if isinstance(attempt, Attempt):
                scored_x.append(index)
                scored_y.append(attempt.reward * 100.0)
            elif include_errored:
                errored_x.append(index)
        visible = scored_x + errored_x
        if visible:
            max_attempts = max(max_attempts, visible[-1])
        if scored_x:
            ax.plot(
                scored_x,
                scored_y,
                color=color,
                marker="o",
                markersize=5.5,
                linewidth=1.8,
                label=pr_series.label,
                zorder=3,
            )
        if errored_x:
            ax.plot(
                errored_x,
                [0.0] * len(errored_x),
                color=color,
                linestyle="none",
                marker="x",
                markersize=7,
                label=None if scored_x else pr_series.label,
                zorder=3,
            )
    ax.set_xticks(list(range(1, max_attempts + 1)))
    ax.set_xlim(0.8, max_attempts + 0.2)
    ax.set_ylim(-6, 106)
    ax.set_yticks(range(0, 101, 10))
    ax.set_xlabel("attempt (run order per PR)")
    ax.set_ylabel("success rate (%)")
    ax.set_title("Success rate per PR across attempts", loc="left", fontsize=12.5, pad=10)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    if len(series) <= 15:
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False, fontsize=9)
    else:
        ax.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, -0.12),
            ncols=min(6, max(1, len(series))),
            frameon=False,
            fontsize=8,
        )
    errored_total = sum(len(s.errored) for s in series)
    note = (
        "y is the Harbor verifier reward (passed / all cases) of each attempt; "
        "x is the attempt order (1st run, 2nd run, ...)."
    )
    if errored_total:
        if include_errored:
            note += " Errored attempts without verifier results are plotted at 0% (\u00d7)."
        else:
            note += f" {errored_total} errored attempt(s) without verifier results are excluded."
    _footer(fig, note)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def _iso_utc(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def write_csv(series: list, out_path: Path) -> None:
    columns = [
        "pr",
        "pr_number",
        "pr_label",
        "attempt",
        "status",
        "success_rate",
        "reward",
        "passed",
        "failed",
        "skipped",
        "missing",
        "total_cases",
        "job",
        "trial",
        "source_folder",
        "container",
        "timestamp_utc",
    ]
    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for pr_series in series:
            for index, attempt in enumerate(pr_series.all_attempts, start=1):
                row = {
                    "pr": attempt.pr,
                    "pr_number": attempt.pr_number,
                    "pr_label": pr_series.label,
                    "attempt": index,
                    "job": attempt.job,
                    "trial": attempt.trial,
                    "source_folder": attempt.source,
                    "container": attempt.container,
                    "timestamp_utc": _iso_utc(attempt.timestamp),
                }
                if isinstance(attempt, Attempt):
                    row.update(
                        status="scored",
                        success_rate=round(attempt.success_rate, 6),
                        reward=attempt.reward,
                        passed=attempt.passed,
                        failed=attempt.failed,
                        skipped=attempt.skipped,
                        missing=attempt.missing,
                        total_cases=attempt.total_cases,
                    )
                else:
                    row.update(status="errored")
                writer.writerow(row)


def print_summary(series: list, scanner: RunScanner, output_paths: list) -> None:
    scored = sum(len(s.attempts) for s in series)
    errored = sum(len(s.errored) for s in series)
    print(
        f"Loaded {scored} scored and {errored} errored attempt(s) across {len(series)} PR(s); "
        f"{scanner.duplicates} duplicate trial(s) skipped."
    )
    print()
    print(f"{'PR':<10} {'runs':>4} {'errored':>7} {'mean passed':>11} {'mean failed':>11} {'mean success':>12}")
    print("-" * 61)
    for pr_series in series:
        if pr_series.attempts:
            mean_pass = statistics.fmean(a.passed for a in pr_series.attempts)
            mean_fail = statistics.fmean(a.failed for a in pr_series.attempts)
            mean_reward = 100.0 * statistics.fmean(a.reward for a in pr_series.attempts)
            print(
                f"{pr_series.label:<10} {len(pr_series.attempts):>4} {len(pr_series.errored):>7} "
                f"{_fmt_count(mean_pass):>11} {_fmt_count(mean_fail):>11} {mean_reward:>11.1f}%"
            )
        else:
            print(
                f"{pr_series.label:<10} {0:>4} {len(pr_series.errored):>7} "
                f"{'-':>11} {'-':>11} {'-':>12}"
            )
    excluded = [s.label for s in series if not s.attempts]
    if excluded:
        print()
        print("No scored attempts (excluded from charts): " + ", ".join(excluded))
    print()
    print("Wrote:")
    for path in output_paths:
        print(f"  {path}")
    for warning in scanner.warnings:
        print(f"note: {warning}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Plot per-PR test outcomes from Harbor completed-run exports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  uv run python scripts/plot_run_tests.py\n"
            "  uv run python scripts/plot_run_tests.py --input out/david --show\n"
        ),
    )
    parser.add_argument(
        "--input",
        action="append",
        metavar="DIR",
        help="run folder or .zip archive to scan; repeatable (default: out/david, out/frank, out/nick)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"directory for the PNG and CSV outputs (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--include-errored",
        action="store_true",
        help="plot errored attempts (no verifier results) at 0%% success in the line chart",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="also show the figures in a window (requires a GUI backend)",
    )
    parser.add_argument("--dpi", type=int, default=160, help="PNG resolution (default: 160)")
    args = parser.parse_args(argv)

    input_paths = []
    for raw in args.input or [str(path) for path in DEFAULT_INPUTS]:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if path not in input_paths:
            input_paths.append(path)

    scanner = RunScanner()
    for path in input_paths:
        scanner.scan(path, path.name)

    if not scanner.attempts:
        print(
            "error: no scored Harbor attempts found under: "
            + ", ".join(str(path) for path in input_paths),
            file=sys.stderr,
        )
        return 2

    series = group_series(scanner.attempts, scanner.errored)
    scored_series = [s for s in series if s.attempts]

    mpl, plt = _load_pyplot(args.show)
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    bar_path = output_dir / "pr_test_share.png"
    line_path = output_dir / "pr_attempt_success_rate.png"
    csv_path = output_dir / "pr_trials.csv"

    plot_stacked_share(mpl, plt, scored_series, bar_path, args.dpi)
    plot_attempt_success(mpl, plt, series, line_path, args.include_errored, args.dpi)
    write_csv(series, csv_path)

    print_summary(series, scanner, [bar_path, line_path, csv_path])
    if args.show:
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
