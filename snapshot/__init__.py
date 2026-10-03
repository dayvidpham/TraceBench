"""TraceBench snapshot package (issue #2).

Build-time view of an arbitrary repo truncated at a date or PR start.

  full history -> cutoff (date | PR start) -> snapshot (tree, history, trace)

Peasant is used as a *binary* (not a library). PR start times come from
``peasant pr show <n> --format json`` by default; the argv template and
JSON keys are configurable because the binary CLI is still evolving.
Tests and offline use can bypass the binary with ``pr_start_override``.
"""

from snapshot.api import Snapshot, snapshot_repo
from snapshot.cutoff import Cutoff
from snapshot.peasant import (
    BinaryPeasantClient,
    PeasantClient,
    PeasantError,
    StubPeasantClient,
)
from snapshot.trace import DirTraceProvider, StubTraceProvider, TraceFile, TraceProvider

__all__ = [
    "BinaryPeasantClient",
    "Cutoff",
    "DirTraceProvider",
    "PeasantClient",
    "PeasantError",
    "Snapshot",
    "StubPeasantClient",
    "StubTraceProvider",
    "TraceFile",
    "TraceProvider",
    "snapshot_repo",
]
