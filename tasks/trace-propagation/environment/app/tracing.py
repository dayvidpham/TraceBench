"""W3C Trace Context helpers (correct — do not modify)."""

from __future__ import annotations

import re
import secrets

TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def parse_traceparent(header: str) -> dict:
    """Parse a W3C traceparent header. Raises ValueError on invalid input."""
    m = TRACEPARENT_RE.match(header.strip())
    if not m:
        raise ValueError(f"invalid traceparent: {header!r}")
    trace_id, span_id, flags = m.groups()
    if trace_id == "0" * 32 or span_id == "0" * 16:
        raise ValueError("all-zero trace-id or span-id is invalid")
    return {"trace_id": trace_id, "span_id": span_id, "sampled": flags == "01"}


def format_traceparent(trace_id: str, span_id: str, sampled: bool) -> str:
    """Format a W3C traceparent header (version 00)."""
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}"


def new_span_id() -> str:
    return secrets.token_hex(8)
