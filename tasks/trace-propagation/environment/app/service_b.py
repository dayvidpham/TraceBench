"""Downstream service (correct — do not modify).

Receives HTTP-style headers, continues the incoming trace by recording the
caller's span as its parent, and reports what it observed.
"""

from __future__ import annotations

from tracing import new_span_id, parse_traceparent


def handle_request(headers: dict) -> dict:
    incoming = parse_traceparent(headers["traceparent"])
    return {
        "trace_id": incoming["trace_id"],
        "parent_span_id": incoming["span_id"],
        "span_id": new_span_id(),
        "sampled": incoming["sampled"],
        "tracestate": headers.get("tracestate"),
    }
