"""Target configurations for benchmark runs.

A target configuration names the harness, model, and thinking level that a
benchmark run uses. The task payload records the configuration so the runner
knows what to execute. The configuration does not filter prior context: the
model receives every prior trace, independent of the harness, model, and
thinking level.

The thinking level is recorded from the spec. The corpus does not carry a
thinking level yet (peasant-labs/schema#147, peasant-labs/peasant#545), so the
payload stubs the corpus side as null when the spec omits it.

The configuration lives in a spec file (YAML when PyYAML is installed, JSON
otherwise) so tests and the product share one source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Accepted thinking levels in a target-configuration spec.
THINKING_LEVELS = ("none", "low", "medium", "high", "xhigh")

_CONFIG_KEYS = {"name", "harness", "model", "thinking"}


@dataclass(frozen=True)
class TargetConfiguration:
    """The harness, model, and thinking level a benchmark run uses."""

    name: str
    harness: str | None = None
    model: str | None = None
    thinking: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The record written to payloads; absent axes are null (stubbed)."""
        return {
            "name": self.name,
            "harness": self.harness,
            "model": self.model,
            "thinking": self.thinking,
        }


def load_target_configs(path: str | Path) -> list[TargetConfiguration]:
    """Load and validate a target-configuration spec."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise ValueError(f"cannot read target configurations {path}: {exc}") from exc
    data = _parse_spec(text, path)
    entries = data.get("target_configurations") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            f"target configurations {path} must carry a non-empty 'target_configurations' list"
        )
    configurations: list[TargetConfiguration] = []
    names: set[str] = set()
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"target configurations {path} entry {position} is not a mapping")
        unknown = set(entry) - _CONFIG_KEYS
        if unknown:
            raise ValueError(
                f"target configurations {path} entry {position} has unknown keys: "
                f"{', '.join(sorted(unknown))}"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"target configurations {path} entry {position} needs a non-empty name")
        if name in names:
            raise ValueError(f"target configurations {path} has duplicate name {name!r}")
        names.add(name)
        for key in ("harness", "model", "thinking"):
            value = entry.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"target configurations {path} entry {name!r}: {key} must be a string")
        thinking = entry.get("thinking")
        if thinking is not None and thinking not in THINKING_LEVELS:
            raise ValueError(
                f"target configurations {path} entry {name!r}: thinking {thinking!r} is outside "
                f"{', '.join(THINKING_LEVELS)}"
            )
        configurations.append(
            TargetConfiguration(
                name=name,
                harness=entry.get("harness"),
                model=entry.get("model"),
                thinking=thinking,
            )
        )
    return configurations


def find_target_config(configurations: list[TargetConfiguration], name: str) -> TargetConfiguration:
    """Select one target configuration by name."""
    for configuration in configurations:
        if configuration.name == name:
            return configuration
    known = ", ".join(configuration.name for configuration in configurations)
    raise ValueError(f"unknown target configuration {name!r}; known configurations: {known}")


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
