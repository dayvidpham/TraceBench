"""Inventory Go test cases at a PR's merged commit and mark changed cases."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


_TEST_FUNCTION = re.compile(r"(?m)^func\s+((?:Test|Fuzz)[A-Z0-9_]\w*|Example\w*)\s*\(")


def build_test_manifest(repo_dir: str | Path, base_commit: str, merge_commit: str) -> dict:
    """Describe every Go test suite in the merged tree, comparing case bodies.

    A case is a top-level Test, Fuzz, or Example function. Dynamic subtests
    cannot be enumerated from source and are reported by the test runner.
    """
    repo = Path(repo_dir)
    base = _git(repo, "rev-parse", "--verify", f"{base_commit}^{{commit}}").decode().strip()
    merged = _git(repo, "rev-parse", "--verify", f"{merge_commit}^{{commit}}").decode().strip()
    base_paths = set(_test_paths(repo, base))
    suites = []
    for path in _test_paths(repo, merged):
        merged_cases = _go_cases(_git(repo, "show", f"{merged}:{path}").decode("utf-8"), path)
        if path in base_paths:
            base_cases = _go_cases(_git(repo, "show", f"{base}:{path}").decode("utf-8"), path)
        else:
            base_cases = {}
        cases = [
            {
                "id": f"{path}::{name}",
                "name": name,
                "golden": name not in base_cases or body != base_cases[name],
                "status": "accept",
            }
            for name, body in sorted(merged_cases.items())
        ]
        suites.append(
            {
                "path": path,
                "framework": "go",
                "package_dir": Path(path).parent.as_posix(),
                "golden": any(case["golden"] for case in cases),
                "cases": cases,
            }
        )
    all_cases = [case for suite in suites for case in suite["cases"]]
    return {
        "schema_version": 1,
        "base_commit": base,
        "merge_commit": merged,
        "case_granularity": "top-level Go test functions; runtime subtests are not enumerated",
        "summary": {
            "suites": len(suites),
            "cases": len(all_cases),
            "golden_cases": sum(case["golden"] for case in all_cases),
        },
        "suites": suites,
    }


def _test_paths(repo: Path, commit: str) -> list[str]:
    raw = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit)
    return sorted(
        path.decode("utf-8")
        for path in raw.split(b"\0")
        if path and path.endswith(b"_test.go")
    )


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(args)} in {repo}: {detail}")
    return result.stdout


def _go_cases(source: str, path: str) -> dict[str, str]:
    masked = _mask_non_code(source)
    cases: dict[str, str] = {}
    for match in _TEST_FUNCTION.finditer(masked):
        name = match.group(1)
        if name == "TestMain":
            continue
        opening = masked.find("{", match.end())
        if opening < 0:
            raise ValueError(f"test function {name} in {path} has no body")
        depth = 0
        closing = -1
        for index in range(opening, len(masked)):
            if masked[index] == "{":
                depth += 1
            elif masked[index] == "}":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing < 0:
            raise ValueError(f"test function {name} in {path} has an unclosed body")
        if name in cases:
            raise ValueError(f"duplicate test function {name} in {path}")
        cases[name] = source[match.start(): closing + 1].replace("\r\n", "\n")
    return cases


def _mask_non_code(source: str) -> str:
    """Blank strings and comments while retaining offsets and newlines."""
    chars = list(source)
    index = 0
    while index < len(chars):
        start = index
        if source.startswith("//", index):
            end = source.find("\n", index)
            index = len(chars) if end < 0 else end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise ValueError("unclosed Go block comment")
            index = end + 2
        elif source[index] in ('"', "'", "`"):
            quote = source[index]
            index += 1
            while index < len(chars):
                if quote != "`" and source[index] == "\\":
                    index += 2
                elif source[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            else:
                raise ValueError("unclosed Go string")
        else:
            index += 1
            continue
        for position in range(start, min(index, len(chars))):
            if chars[position] != "\n":
                chars[position] = " "
    return "".join(chars)
