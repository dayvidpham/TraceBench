"""Repository adaptation spec: the test and build command per repository.

A repository spec names, for each repository a task comes from, the test
framework plus the command that runs the tests and the command that builds the
code. The verifier runs the test command; the oracle solution runs the build
command. The spec is shaped like the target-configuration spec (YAML when
PyYAML is installed, JSON otherwise). One entry per repository; the first
``match`` wins. ``DEFAULT_REPOSITORY_SPECS`` carries the Go/Peasant default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .target_config import _parse_spec
from .verifier import DEFAULT_TEST_COMMAND

_SPEC_KEYS = {"match", "framework", "test_command", "build_command"}


@dataclass(frozen=True)
class RepositorySpec:
    """How to test and build one repository."""

    match: str
    framework: str
    test_command: str
    build_command: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "match": self.match,
            "framework": self.framework,
            "test_command": self.test_command,
            "build_command": self.build_command,
        }


_GO_TEST = DEFAULT_TEST_COMMAND
_GO_BUILD = "go build ./..."

#: The shipped Go/Peasant default.
DEFAULT_REPOSITORY_SPECS: tuple[RepositorySpec, ...] = (
    RepositorySpec("peasant-labs/peasant", "go", _GO_TEST, _GO_BUILD),
    RepositorySpec("peasant-labs/peasant-prerelease-archive", "go", _GO_TEST, _GO_BUILD),
)


def load_repository_specs(path: str | Path | None = None) -> list[RepositorySpec]:
    """Load and validate a repository spec; with no path, return the shipped default."""
    if path is None:
        return list(DEFAULT_REPOSITORY_SPECS)
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise ValueError(f"cannot read repository spec {path}: {exc}") from exc
    data = _parse_spec(text, path)
    entries = data.get("repositories") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            f"repository spec {path} must carry a non-empty 'repositories' list "
            "(for example: repositories: [{match: owner/repo, framework: go, "
            "test_command: ..., build_command: ...}])"
        )
    specs: list[RepositorySpec] = []
    matches: set[str] = set()
    for position, entry in enumerate(entries):
        where = f"repository spec {path} entry {position}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} is not a mapping; give match, framework, test_command, build_command")
        unknown = set(entry) - _SPEC_KEYS
        if unknown:
            raise ValueError(
                f"{where} has unknown keys: {', '.join(sorted(unknown))}; "
                f"allowed keys: {', '.join(sorted(_SPEC_KEYS))}"
            )
        match = entry.get("match")
        if not isinstance(match, str) or not match:
            raise ValueError(f"{where} needs a non-empty string 'match' (owner/repo)")
        where = f"repository spec {path} entry {position} ({match!r})"
        if match in matches:
            raise ValueError(f"{where} duplicates match {match!r}; keep one entry per repository")
        matches.add(match)
        for key in ("framework", "test_command", "build_command"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{where} needs a non-empty string {key!r}")
        specs.append(
            RepositorySpec(
                match=match,
                framework=entry["framework"],
                test_command=entry["test_command"],
                build_command=entry["build_command"],
            )
        )
    return specs


def find_repository_spec(specs: list[RepositorySpec] | tuple[RepositorySpec, ...], repo: str) -> RepositorySpec:
    """Select the spec for ``repo`` (owner/name); the first match wins."""
    for spec in specs:
        if spec.match == repo:
            return spec
    known = ", ".join(spec.match for spec in specs) or "(none)"
    raise ValueError(
        f"no repository spec matches {repo!r}; known matches: {known}. "
        "Add an entry for it to the repository spec."
    )
