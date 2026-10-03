"""Peasant integration via binary (C4: external container).

Default contract (overridable — the binary CLI is still evolving):

    peasant pr show <n> --format json

Expected stdout: JSON object (or {"pr": {...}} / list with one element)
containing one of these keys: started_at, created_at, createdAt,
start_time, startedAt. First match wins, parsed as ISO-8601.

Version pin: run ``peasant --version`` at startup; if ``expected_version``
is given and the output does not contain it, raise PeasantError.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence


class PeasantError(RuntimeError):
    pass


DEFAULT_STARTED_AT_KEYS: tuple[str, ...] = (
    "started_at",
    "created_at",
    "createdAt",
    "start_time",
    "startedAt",
)


def _parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _extract_started_at(
    payload: object, keys: Sequence[str] = DEFAULT_STARTED_AT_KEYS
) -> datetime:
    obj: object = payload
    if isinstance(obj, list):
        if not obj:
            raise PeasantError("peasant pr show returned empty list")
        obj = obj[0]
    if isinstance(obj, dict) and "pr" in obj and isinstance(obj["pr"], dict):
        obj = obj["pr"]
    if not isinstance(obj, dict):
        raise PeasantError(f"unexpected peasant JSON shape: {type(obj).__name__}")
    for key in keys:
        if key in obj and obj[key]:
            try:
                return _parse_time(str(obj[key]))
            except ValueError as exc:
                raise PeasantError(
                    f"peasant returned unparsable time {obj[key]!r} for key {key!r}"
                ) from exc
    raise PeasantError(
        f"peasant JSON has none of the time keys {list(keys)}; got {sorted(obj)}"
    )


class PeasantClient(Protocol):
    def pr_start(self, pr_number: int) -> datetime: ...
    def version(self) -> str: ...


@dataclass
class BinaryPeasantClient:
    """Call the peasant binary via subprocess."""

    binary: str = "peasant"
    timeout_sec: float = 30.0
    expected_version: str | None = None
    pr_show_argv: Sequence[str] | None = None
    started_at_keys: Sequence[str] = DEFAULT_STARTED_AT_KEYS

    def _run(self, argv: Sequence[str]) -> str:
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
        except FileNotFoundError as exc:
            raise PeasantError(f"peasant binary not found: {self.binary!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PeasantError(
                f"peasant timed out after {self.timeout_sec}s: {' '.join(argv)}"
            ) from exc
        if proc.returncode != 0:
            raise PeasantError(
                f"peasant failed (exit {proc.returncode}): {' '.join(argv)}\n"
                f"stderr: {proc.stderr.strip()[:2000]}"
            )
        return proc.stdout

    def version(self) -> str:
        out = self._run([self.binary, "--version"]).strip()
        if self.expected_version and self.expected_version not in out:
            raise PeasantError(
                f"peasant version mismatch: expected {self.expected_version!r} "
                f"in {out!r}"
            )
        return out

    def pr_start(self, pr_number: int) -> datetime:
        if pr_number <= 0:
            raise PeasantError(f"invalid PR number: {pr_number}")
        template = (
            list(self.pr_show_argv)
            if self.pr_show_argv is not None
            else [self.binary, "pr", "show", "{pr}", "--format", "json"]
        )
        argv = [a.replace("{pr}", str(pr_number)) for a in template]
        out = self._run(argv)
        try:
            payload = json.loads(out)
        except json.JSONDecodeError as exc:
            raise PeasantError(
                f"peasant returned non-JSON output: {out[:500]!r}"
            ) from exc
        return _extract_started_at(payload, self.started_at_keys)


@dataclass
class StubPeasantClient:
    """In-memory fake for tests / offline use."""

    starts: dict[int, datetime]
    version_str: str = "stub"

    def version(self) -> str:
        return self.version_str

    def pr_start(self, pr_number: int) -> datetime:
        try:
            dt = self.starts[pr_number]
        except KeyError as exc:
            raise PeasantError(f"stub has no PR {pr_number}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
