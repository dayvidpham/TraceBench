"""Inventory Go test cases at a PR's merged commit and mark changed cases."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .blobs import read_blobs

_TEST_FUNCTION = re.compile(r"(?m)^func\s+((?:Test|Fuzz)[A-Z0-9_]\w*|Example\w*)\s*\(")
_GO_BUILD_LINE = re.compile(r"^\s*//go:build\s+(.+?)\s*$")
_LEGACY_BUILD_LINE = re.compile(r"^\s*//\s*\+build\s+(.+?)\s*$")

#: GOOS values Go recognizes; the task container builds for linux.
_GOOS = frozenset({
    "aix", "android", "darwin", "dragonfly", "freebsd", "hurd", "illumos",
    "ios", "js", "linux", "nacl", "netbsd", "openbsd", "plan9", "solaris",
    "wasip1", "windows", "zos",
})
#: GOARCH values Go recognizes; the task container builds for amd64.
_GOARCH = frozenset({
    "386", "amd64", "arm", "arm64", "loong64", "mips", "mips64", "mips64le",
    "mipsle", "ppc64", "ppc64le", "riscv64", "s390x", "sparc64", "wasm",
})
#: Go toolchain tags satisfied up to the version the task images build with.
_GO_VERSION = (1, 25)
#: Build tags satisfied by default in the task container.
_SATISFIED_TAGS = frozenset({"unix", "cgo", "gc"})
#: Build tags never satisfied in the task container.
_UNSATISFIED_TAGS = frozenset({"gccgo", "boringcrypto"})


def build_test_manifest(repo_dir: str | Path, base_commit: str, merge_commit: str) -> dict:
    """Describe every Go test suite in the merged tree, comparing case bodies.

    A case is a top-level Test, Fuzz, or Example function. Dynamic subtests
    cannot be enumerated from source and are reported by the test runner.
    """
    repo = Path(repo_dir)
    base = _git(repo, "rev-parse", "--verify", f"{base_commit}^{{commit}}").decode().strip()
    merged = _git(repo, "rev-parse", "--verify", f"{merge_commit}^{{commit}}").decode().strip()
    base_paths = set(_test_paths(repo, base))
    merged_paths = _test_paths(repo, merged)
    base_blobs = read_blobs(repo, base, sorted(base_paths))
    merged_blobs = read_blobs(repo, merged, merged_paths)
    suites = []
    for path in merged_paths:
        source = merged_blobs[path].decode("utf-8")
        merged_cases = _go_cases(source, path)
        constraint = build_constraint(source)
        accepted = constraint_satisfied(constraint)
        if path in base_paths:
            base_cases = _go_cases(base_blobs[path].decode("utf-8"), path)
        else:
            base_cases = {}
        cases = [
            {
                "id": f"{path}::{name}",
                "name": name,
                "golden": name not in base_cases or body != base_cases[name],
                "status": "accept" if accepted else "reject",
                **(
                    {
                        "reject_reason": (
                            "build constraint not satisfied by the test command "
                            f"(no custom tags): {constraint}"
                        )
                    }
                    if not accepted
                    else {}
                ),
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
            "rejected_cases": sum(case["status"] == "reject" for case in all_cases),
        },
        "suites": suites,
    }


def build_constraint(source: str) -> str | None:
    """The build constraint expression of a Go source file, or ``None``."""
    header = source.split("\npackage ", 1)[0]
    for line in header.splitlines():
        match = _GO_BUILD_LINE.match(line)
        if match:
            return match.group(1)
    legacy = [
        match.group(1)
        for line in header.splitlines()
        if (match := _LEGACY_BUILD_LINE.match(line))
    ]
    if legacy:
        return " && ".join(f"({_legacy_expression(line)})" for line in legacy)
    return None


def _legacy_expression(line: str) -> str:
    # Legacy syntax: space-separated options are ORed; comma-separated terms
    # within an option are ANDed.
    options = [" && ".join(group.split(",")) for group in line.split()]
    return " || ".join(f"({option})" for option in options)


def constraint_satisfied(expression: str | None) -> bool:
    """Whether the task container's default build context satisfies a constraint.

    The context is linux/amd64 with the default tags (``cgo``, ``gc``, ``unix``)
    and no custom tags: the task's test command runs without ``-tags``.
    """
    if expression is None:
        return True
    tokens = re.findall(r"&&|\|\||[!()]|[^\s!()&|]+", expression)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def parse_or() -> bool:
        nonlocal position
        value = parse_and()
        while peek() == "||":
            position += 1
            value = parse_and() or value
        return value

    def parse_and() -> bool:
        nonlocal position
        value = parse_unary()
        while peek() == "&&":
            position += 1
            value = parse_unary() and value
        return value

    def parse_unary() -> bool:
        nonlocal position
        token = peek()
        if token == "!":
            position += 1
            return not parse_unary()
        if token == "(":
            position += 1
            value = parse_or()
            if peek() == ")":
                position += 1
            return value
        position += 1
        return _tag_satisfied(token or "")

    return parse_or()


def _tag_satisfied(tag: str) -> bool:
    if tag in _GOOS:
        return tag == "linux"
    if tag in _GOARCH:
        return tag == "amd64"
    if tag in _SATISFIED_TAGS:
        return True
    if tag in _UNSATISFIED_TAGS:
        return False
    version = re.fullmatch(r"go1\.(\d+)", tag)
    if version:
        return (1, int(version.group(1))) <= _GO_VERSION
    return False


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
