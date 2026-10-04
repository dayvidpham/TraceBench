"""Golden-suite verifier: grade a ``go test -json`` run against a test manifest.

This module is standard-library only. The skeleton ships a copy into each
task's ``tests/`` directory, and ``tests/test.sh`` runs it inside the task
image with ``python3 /tests/verifier.py run``.

The flow of ``run``:

1. Delete the pre-PR test files that match the removal expressions in
   ``verifier-config.json`` (compiled by the canonical doublestar matcher).
2. Overlay the golden suite into the workspace.
3. Run the repository test command (``go test -json -count=1 ./...`` by default).
4. Parse the JSON stream; keep one final action per top-level test.
5. Join the results to the manifest on ``(package_dir, test name)``.
6. Write ``reward.txt`` (``passed / total``) and ``test-results.json``.

The verifier fails closed: a missing manifest, an unknown manifest schema
version, an unparsable stream line, a killed test command, or a truncated
stream gives reward 0 and records the reason. The exit code of the test
command never decides the reward.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

#: Version of the ``test-results.json`` layout this module writes.
REPORT_SCHEMA_VERSION = 1
#: Manifest ``schema_version`` values this verifier understands.
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = frozenset({1})
#: The closed set of per-case outcomes in ``test-results.json``.
OUTCOMES = ("pass", "fail", "skip", "missing")
#: ``go test -json`` actions that decide a test's outcome.
DECIDING_ACTIONS = frozenset({"pass", "fail", "skip"})
#: Known ``go test -json`` actions that never decide an outcome.
PROGRESS_ACTIONS = frozenset({"start", "run", "pause", "cont", "output", "bench", "build-output", "build-fail"})
DEFAULT_TEST_COMMAND = "go test -json -count=1 ./..."
CONFIG_NAME = "verifier-config.json"
MANIFEST_FILE = "test-manifest.json"
REWARD_FILE = "reward.txt"
REPORT_FILE = "test-results.json"
MISSING_MANIFEST = "test manifest is missing; the task must ship tests/test-manifest.json"


@dataclass
class ParsedStream:
    """The decided state of a ``go test -json`` stream.

    ``tests`` maps ``(import path, top-level test name)`` to the final
    deciding action, or to ``"unknown:<action>"`` when the last decisive event
    carried an action this verifier does not know. ``error`` is set when the
    stream cannot be trusted (unparsable or truncated).
    """

    tests: dict[tuple[str, str], str] = field(default_factory=dict)
    failed_packages: set[str] = field(default_factory=set)
    finished_packages: set[str] = field(default_factory=set)
    seen_packages: set[str] = field(default_factory=set)
    error: str | None = None


def parse_go_test_json(text: str) -> ParsedStream:
    """Parse a ``go test -json`` stream, keeping top-level tests only."""
    parsed = ParsedStream()
    lines = text.split("\n")
    if text and not text.endswith("\n"):
        parsed.error = (
            "truncated test stream: the last line has no newline terminator "
            f"({lines[-1][:80]!r}); the test command was likely interrupted"
        )
        return parsed
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            parsed.error = (
                f"unparsable test stream line {number}: {exc.msg} ({line[:80]!r}); "
                "the test command must emit `go test -json` output only"
            )
            return parsed
        if not isinstance(event, dict) or not isinstance(event.get("Action"), str):
            parsed.error = (
                f"unparsable test stream line {number}: not a `go test -json` event "
                f"with a string Action ({line[:80]!r})"
            )
            return parsed
        action = event["Action"]
        package = event.get("Package") or ""
        test = event.get("Test") or ""
        if not package:
            # Build-output events carry ImportPath, not Package; they never decide.
            continue
        parsed.seen_packages.add(package)
        if not test:
            if action in DECIDING_ACTIONS:
                parsed.finished_packages.add(package)
                if action == "fail":
                    parsed.failed_packages.add(package)
            continue
        if "/" in test:
            continue
        if action in DECIDING_ACTIONS:
            parsed.tests[(package, test)] = action
        elif action not in PROGRESS_ACTIONS:
            parsed.tests[(package, test)] = f"unknown:{action}"
    unfinished = sorted(parsed.seen_packages - parsed.finished_packages)
    if unfinished:
        parsed.error = (
            "truncated test stream: no final package result for "
            + ", ".join(unfinished)
            + "; the test command was likely interrupted"
        )
    return parsed


def read_module_path(go_mod: Path) -> str:
    """The ``module`` path declared in a ``go.mod`` file."""
    for line in go_mod.read_text().splitlines():
        stripped = line.strip()
        stripped = stripped.split("//", 1)[0].strip()
        if stripped.startswith("module ") or stripped.startswith("module\t"):
            parts = stripped.split(None, 1)
            module = parts[1].strip().strip('"') if len(parts) == 2 else ""
            if module:
                return module
    raise ValueError(
        f"{go_mod} declares no module path; the verifier needs it to join "
        "test results to manifest cases"
    )


def import_path_of(directory: str, module: str) -> str:
    """Go import path of a repository-relative package directory."""
    return module if directory == "." else f"{module}/{directory}"


def manifest_cases(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a test manifest into case records with their package directory.

    Raises ``ValueError`` when a suite or case has the wrong shape.
    """
    cases = []
    suites = manifest.get("suites") or []
    if not isinstance(suites, list):
        raise ValueError(f"test manifest suites is {type(suites).__name__}, not a list; regenerate the task")
    for suite in suites:
        suite_cases = suite.get("cases") if isinstance(suite, dict) else None
        if not isinstance(suite_cases, list):
            raise ValueError(f"test manifest suite {suite!r:.80} has no cases list; regenerate the task")
        for case in suite_cases:
            case_id = case.get("id") if isinstance(case, dict) else None
            if not isinstance(case_id, str) or "::" not in case_id:
                raise ValueError(
                    f"test manifest case {case!r:.80} has no `<path>::<name>` id; regenerate the task"
                )
            path, _, name = case_id.rpartition("::")
            directory = os.path.dirname(path) or "."
            cases.append(
                {"id": case_id, "package_dir": directory, "name": name, "golden": bool(case.get("golden"))}
            )
    return cases


def check_manifest(manifest: Any) -> str | None:
    """Why a manifest cannot be graded, or ``None`` when it can."""
    if not isinstance(manifest, dict):
        return f"test manifest is not a JSON object (got {type(manifest).__name__}); regenerate the task"
    version = manifest.get("schema_version")
    if version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
        return (
            f"unknown test manifest schema_version {version!r}; this verifier supports "
            f"{sorted(SUPPORTED_MANIFEST_SCHEMA_VERSIONS)}. Regenerate the task with a matching loader"
        )
    total = (manifest.get("summary") or {}).get("cases")
    if not isinstance(total, int) or total <= 0:
        return (
            f"test manifest summary.cases is {total!r}; a task with no test cases is a build "
            "error. Regenerate the manifest from the merge commit"
        )
    return None


def grade(
    manifest: Any,
    stream: str | None,
    module: str | None,
    *,
    test_command: str = DEFAULT_TEST_COMMAND,
    exit_code: int | None = None,
    duration_sec: float = 0.0,
    pr: str = "",
    failure: str | None = None,
) -> tuple[float, dict[str, Any]]:
    """Grade a test stream against a manifest; return ``(reward, report)``.

    ``failure`` is a fail-closed reason found before parsing (for example a
    killed test command); it forces reward 0.
    """
    reasons: list[str] = []
    if failure:
        reasons.append(failure)
    manifest_error = check_manifest(manifest) if manifest is not None else MISSING_MANIFEST
    if manifest_error and manifest_error != failure:
        reasons.append(manifest_error)
    usable = manifest_error is None
    cases: list[dict[str, Any]] = []
    if usable:
        try:
            cases = manifest_cases(manifest)
        except ValueError as exc:
            reasons.append(str(exc))
            usable = False
    total = manifest["summary"]["cases"] if usable else 0
    if usable and len(cases) != total:
        reasons.append(
            f"test manifest lists {len(cases)} cases but summary.cases is {total}; regenerate the manifest"
        )

    parsed = parse_go_test_json(stream) if stream is not None else ParsedStream()
    if parsed.error:
        reasons.append(parsed.error)
    if stream is None and not failure:
        reasons.append("no test stream was produced")

    entries = []
    for case in cases:
        outcome = "missing"
        if module is not None:
            import_path = import_path_of(case["package_dir"], module)
            action = parsed.tests.get((import_path, case["name"]))
            if action in DECIDING_ACTIONS:
                outcome = action
            elif action is not None:
                outcome = "fail"  # unknown final action: not passed
            elif import_path in parsed.failed_packages:
                outcome = "fail"  # package build failure
        entries.append({**case, "outcome": outcome})

    counts = {name: sum(entry["outcome"] == name for entry in entries) for name in OUTCOMES}
    reward = counts["pass"] / total if total and not reasons else 0.0
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "pr": pr,
        "base_commit": manifest.get("base_commit", "") if isinstance(manifest, dict) else "",
        "merge_commit": manifest.get("merge_commit", "") if isinstance(manifest, dict) else "",
        "manifest_schema_version": manifest.get("schema_version") if isinstance(manifest, dict) else None,
        "test_command": test_command,
        "exit_code": exit_code,
        "duration_sec": round(duration_sec, 3),
        "total_cases": total,
        "passed": counts["pass"],
        "failed": counts["fail"],
        "skipped": counts["skip"],
        "missing": counts["missing"],
        "golden_flagged": sum(entry["golden"] for entry in entries),
        "golden_flagged_passed": sum(entry["golden"] and entry["outcome"] == "pass" for entry in entries),
        "failed_test_ids": [entry["id"] for entry in entries if entry["outcome"] == "fail"],
        "reward": reward,
        "fail_closed_reasons": reasons,
        "entries": entries,
    }
    return reward, report


def write_results(log_dir: Path, reward: float, report: dict[str, Any]) -> None:
    """Write exactly ``reward.txt`` and ``test-results.json``."""
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / REWARD_FILE).write_text(f"{reward}\n")
    (log_dir / REPORT_FILE).write_text(json.dumps(report, indent=2) + "\n")


def remove_test_files(app_dir: Path, expressions: Iterable[str]) -> list[str]:
    """Delete workspace files whose repository-relative path matches any expression."""
    compiled = [re.compile(expression, re.DOTALL) for expression in expressions]
    removed = []
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [name for name in dirs if name != ".git"]
        for name in files:
            path = Path(root) / name
            relative = path.relative_to(app_dir).as_posix()
            if any(pattern.match(relative) for pattern in compiled):
                path.unlink()
                removed.append(relative)
    return sorted(removed)


def overlay(golden_dir: Path, app_dir: Path) -> int:
    """Copy the golden suite over the workspace; return the number of files."""
    count = 0
    for path in sorted(golden_dir.rglob("*")):
        if path.is_file():
            target = app_dir / path.relative_to(golden_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                target.unlink()
            shutil.copy2(path, target)
            count += 1
    return count


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _as_bytes(data: bytes | str | None) -> bytes:
    if data is None:
        return b""
    return data.encode() if isinstance(data, str) else data


def write_minimal_report(log_dir: Path, reasons: list[str]) -> None:
    """Write a reward-0 report when the verifier itself crashed."""
    write_results(log_dir, 0.0, {"schema_version": REPORT_SCHEMA_VERSION, "reward": 0.0, "fail_closed_reasons": reasons})


def run(
    *,
    tests_dir: Path,
    app_dir: Path,
    golden_dir: Path,
    log_dir: Path,
    timeout_sec: float | None = None,
) -> float:
    """Execute the full verifier flow and write the results; return the reward."""
    config_path = tests_dir / CONFIG_NAME
    config: dict[str, Any] = {}
    failure = None
    if config_path.is_file():
        try:
            config = _load_json(config_path)
        except (OSError, json.JSONDecodeError) as exc:
            failure = f"cannot read verifier config {config_path}: {exc}; regenerate the task"
        if failure is None and not isinstance(config, dict):
            failure = f"verifier config {config_path} is not a JSON object; regenerate the task"
            config = {}
    test_command = config.get("test_command") or DEFAULT_TEST_COMMAND
    pr = config.get("pr", "")
    manifest_path = tests_dir / MANIFEST_FILE
    manifest = None
    if failure is None:
        if not manifest_path.is_file():
            failure = MISSING_MANIFEST
        else:
            try:
                manifest = _load_json(manifest_path)
            except (OSError, json.JSONDecodeError) as exc:
                failure = f"cannot read test manifest {manifest_path}: {exc}; regenerate the task"

    def finish(reason: str, **kwargs: Any) -> float:
        reward, report = grade(manifest, None, None, test_command=test_command, pr=pr, failure=reason, **kwargs)
        write_results(log_dir, reward, report)
        print(f"tracebench: {reason}", file=sys.stderr)
        return reward

    if failure:
        return finish(failure)
    if not golden_dir.is_dir() or not any(golden_dir.iterdir()):
        return finish(f"golden tests are not materialized in {golden_dir}; build the task from a full payload")
    if not app_dir.is_dir():
        return finish(f"workspace {app_dir} does not exist; the environment did not upload the repository")

    remove_test_files(app_dir, config.get("remove_regexes") or [])
    overlay(golden_dir, app_dir)

    go_mod = app_dir / "go.mod"
    module = None
    try:
        module = read_module_path(go_mod)
    except (OSError, ValueError) as exc:
        return finish(f"cannot read the module path: {exc}")

    start = time.monotonic()
    try:
        completed = subprocess.run(
            test_command, shell=True, cwd=app_dir, capture_output=True, timeout=timeout_sec
        )
    except subprocess.TimeoutExpired as exc:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "test-stdout.jsonl").write_bytes(_as_bytes(exc.stdout))
        (log_dir / "test-stderr.txt").write_bytes(_as_bytes(exc.stderr))
        return finish(
            f"test command `{test_command}` timed out after {timeout_sec}s and was killed",
            duration_sec=time.monotonic() - start,
        )
    duration = time.monotonic() - start
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "test-stdout.jsonl").write_bytes(completed.stdout)
    (log_dir / "test-stderr.txt").write_bytes(completed.stderr)
    killed = None
    if completed.returncode < 0 or completed.returncode > 128:
        # Negative: killed directly; above 128: the shell reports a killed child.
        signal_number = -completed.returncode if completed.returncode < 0 else completed.returncode - 128
        killed = f"test command `{test_command}` was killed by signal {signal_number}"
    reward, report = grade(
        manifest,
        completed.stdout.decode("utf-8", errors="replace"),
        module,
        test_command=test_command,
        exit_code=completed.returncode,
        duration_sec=duration,
        pr=pr,
        failure=killed,
    )
    write_results(log_dir, reward, report)
    for reason in report["fail_closed_reasons"]:
        print(f"tracebench: {reason}", file=sys.stderr)
    return reward


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verifier.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="run the full verifier flow")
    run_parser.add_argument("--tests-dir", default="/tests")
    run_parser.add_argument("--app-dir", default="/workdir/repo")
    run_parser.add_argument("--golden-dir", default=None, help="default: <tests-dir>/golden")
    run_parser.add_argument("--log-dir", default="/logs/verifier")
    run_parser.add_argument("--timeout-sec", type=float, default=None)
    grade_parser = commands.add_parser("grade", help="grade an existing go test -json stream")
    grade_parser.add_argument("--manifest", required=True)
    grade_parser.add_argument("--stream", required=True)
    grade_parser.add_argument("--go-mod", required=True)
    grade_parser.add_argument("--log-dir", required=True)
    args = parser.parse_args(argv)

    if args.command == "run":
        tests_dir = Path(args.tests_dir)
        try:
            run(
                tests_dir=tests_dir,
                app_dir=Path(args.app_dir),
                golden_dir=Path(args.golden_dir) if args.golden_dir else tests_dir / "golden",
                log_dir=Path(args.log_dir),
                timeout_sec=args.timeout_sec,
            )
        except Exception as exc:  # fail closed: a crash still leaves a reward-0 report
            reason = f"verifier crashed: {type(exc).__name__}: {exc}"
            write_minimal_report(Path(args.log_dir), [reason])
            print(f"tracebench: {reason}", file=sys.stderr)
            raise
        return 0
    manifest_path = Path(args.manifest)
    manifest = _load_json(manifest_path) if manifest_path.is_file() else None
    reward, report = grade(
        manifest, Path(args.stream).read_text(), read_module_path(Path(args.go_mod))
    )
    write_results(Path(args.log_dir), reward, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
