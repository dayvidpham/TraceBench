"""Upstream service — CONTAINS THE BUG. Fix `call_downstream`.

Correct behavior per W3C Trace Context:
  * propagate the incoming `trace_id` unchanged (do NOT mint a new one),
  * generate a fresh `span_id` for the outgoing call,
  * preserve the `sampled` flag,
  * forward `tracestate` unchanged when present.
"""

from __future__ import annotations

import secrets

import service_b
from tracing import format_traceparent, new_span_id, parse_traceparent


def call_downstream(trace_id: str, span_id: str, sampled: bool,
                    tracestate: str | None, downstream) -> dict:
    # BUG: mints a fresh trace-id, resets the sampled flag, and drops
    # tracestate — breaking distributed-trace correlation.
    fresh_trace = secrets.token_hex(16)
    headers = {"traceparent": format_traceparent(fresh_trace, new_span_id(), False)}
    return downstream(headers)


def handle_incoming(headers: dict, downstream=service_b.handle_request) -> dict:
    """Entry point: continue the incoming trace and call downstream."""
    incoming = parse_traceparent(headers["traceparent"])
    my_span = new_span_id()
    result = call_downstream(
        incoming["trace_id"],
        my_span,
        incoming["sampled"],
        headers.get("tracestate"),
        downstream,
    )
    result["caller_span_id"] = my_span
    result["incoming_span_id"] = incoming["span_id"]
    return result
