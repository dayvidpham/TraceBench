"""Target configurations a task can be adapted to.

An adaptation names the harness, model, and thinking level a task run targets.
Task payloads record it and, when it is given, keep only prior sessions whose
metadata matches the specified axes. The configuration lives in a spec file
(YAML when PyYAML is installed, JSON otherwise) so tests and the product share
one source.

Thinking levels are a closed set. The corpus contract does not carry a level,
so matching derives it: a session with declared ``thinkingLevel`` metadata (an
extension key) matches exactly; otherwise a session with ``stats.thoughtTokens``
is treated as thinking and one without as ``none``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Accepted thinking levels in an adaptation spec.
THINKING_LEVELS = ("none", "low", "medium", "high", "xhigh")

_ADAPTATION_KEYS = {"name", "harness", "model", "thinking"}


@dataclass(frozen=True)
class Adaptation:
    """A harness/model/thinking configuration a task run targets."""

    name: str
    harness: str | None = None
    model: str | None = None
    thinking: str | None = None

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {"name": self.name}
        if self.harness is not None:
            record["harness"] = self.harness
        if self.model is not None:
            record["model"] = self.model
        if self.thinking is not None:
            record["thinking"] = self.thinking
        return record

    def matches(self, metadata: dict[str, Any] | None) -> bool:
        """Whether a session's metadata satisfies the specified axes."""
        if metadata is None:
            return False
        if self.harness is not None and metadata.get("harness") != self.harness:
            return False
        if self.model is not None and metadata.get("model") != self.model:
            return False
        if self.thinking is not None and not thinking_matches(self.thinking, metadata):
            return False
        return True


def thinking_matches(level: str, metadata: dict[str, Any]) -> bool:
    """Whether a session matches a required thinking level."""
    actual = session_thinking(metadata)
    if level == "none":
        return actual == "none"
    return actual in (level, "thinking")


def session_thinking(metadata: dict[str, Any]) -> str:
    """Derive a session's thinking level from its metadata."""
    declared = metadata.get("thinkingLevel")
    if isinstance(declared, str) and declared:
        return declared
    thought = metadata.get("stats", {}).get("thoughtTokens")
    return "none" if not thought else "thinking"


def load_adaptations(path: str | Path) -> list[Adaptation]:
    """Load and validate an adaptation spec."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise ValueError(f"cannot read adaptations {path}: {exc}") from exc
    data = _parse_spec(text, path)
    entries = data.get("adaptations") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"adaptations {path} must carry a non-empty 'adaptations' list")
    adaptations: list[Adaptation] = []
    names: set[str] = set()
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"adaptations {path} entry {position} is not a mapping")
        unknown = set(entry) - _ADAPTATION_KEYS
        if unknown:
            raise ValueError(
                f"adaptations {path} entry {position} has unknown keys: {', '.join(sorted(unknown))}"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"adaptations {path} entry {position} needs a non-empty name")
        if name in names:
            raise ValueError(f"adaptations {path} has duplicate name {name!r}")
        names.add(name)
        for key in ("harness", "model", "thinking"):
            value = entry.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"adaptations {path} entry {name!r}: {key} must be a string")
        thinking = entry.get("thinking")
        if thinking is not None and thinking not in THINKING_LEVELS:
            raise ValueError(
                f"adaptations {path} entry {name!r}: thinking {thinking!r} is outside "
                f"{', '.join(THINKING_LEVELS)}"
            )
        adaptations.append(
            Adaptation(name=name, harness=entry.get("harness"), model=entry.get("model"), thinking=thinking)
        )
    return adaptations


def find_adaptation(adaptations: list[Adaptation], name: str) -> Adaptation:
    """Select one adaptation by name."""
    for adaptation in adaptations:
        if adaptation.name == name:
            return adaptation
    known = ", ".join(adaptation.name for adaptation in adaptations)
    raise ValueError(f"unknown adaptation {name!r}; known adaptations: {known}")


def _parse_spec(text: str, path: str | Path) -> Any:
    try:
        import yaml
    except ImportError:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"cannot decode {path} as JSON; install PyYAML (tracebench-corpus[test]) for YAML specs: {exc}"
            ) from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"cannot decode {path}: {exc}") from exc
