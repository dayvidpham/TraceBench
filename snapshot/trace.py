"""Trace collection (C4: TraceCollector).

Trace store is not available yet. Contract for the future: a bunch of files
the coding agent can access during execution. This module defines the
interface plus two implementations usable today:

- StubTraceProvider: returns [] (default; keeps #2 unblocked).
- DirTraceProvider: walks a directory, uses file mtime as event time, keeps
  files with mtime <= cutoff. This matches "bunch of files" and lets us test
  the cutoff rule before the real store lands.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TraceFile:
    path: str  # repo-relative / trace-relative path
    event_time: datetime
    size: int


class TraceProvider(Protocol):
    def list_files(self, cutoff: datetime) -> list[TraceFile]: ...


@dataclass
class StubTraceProvider:
    def list_files(self, cutoff: datetime) -> list[TraceFile]:
        return []


@dataclass
class DirTraceProvider:
    root: Path

    def list_files(self, cutoff: datetime) -> list[TraceFile]:
        cutoff = cutoff.astimezone(timezone.utc)
        out: list[TraceFile] = []
        if not self.root.exists():
            return out
        for dirpath, _, filenames in os.walk(self.root):
            for name in filenames:
                full = Path(dirpath) / name
                try:
                    st = full.stat()
                except FileNotFoundError:
                    continue
                event = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                if event <= cutoff:
                    out.append(
                        TraceFile(
                            path=str(full.relative_to(self.root)),
                            event_time=event,
                            size=st.st_size,
                        )
                    )
        out.sort(key=lambda t: t.path)
        return out


def materialize_traces(
    provider_root: Path | None, files: list[TraceFile], dest: Path
) -> None:
    if provider_root is None:
        return
    for entry in files:
        src = provider_root / entry.path
        dst = dest / entry.path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
