"""Grading tests for trace-propagation. Uses randomized ids per run so
hardcoded golden outputs cannot pass. Run from /app (imports service_a)."""

import re
import secrets
import sys

import pytest

sys.path.insert(0, "/app")

import service_a  # noqa: E402
from tracing import format_traceparent  # noqa: E402

TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def run_chain(sampled: bool, tracestate: str | None = None) -> tuple[dict, str]:
    trace_id = secrets.token_hex(16)
    headers = {"traceparent": format_traceparent(trace_id, secrets.token_hex(8), sampled)}
    if tracestate is not None:
        headers["tracestate"] = tracestate
    return service_a.handle_incoming(headers), trace_id


def test_trace_id_preserved_sampled():
    result, trace_id = run_chain(sampled=True)
    assert result["trace_id"] == trace_id


def test_trace_id_preserved_unsampled():
    result, trace_id = run_chain(sampled=False)
    assert result["trace_id"] == trace_id


def test_parent_linkage():
    result, _ = run_chain(sampled=True)
    assert result["parent_span_id"] == result["caller_span_id"]
    assert result["parent_span_id"] != result["incoming_span_id"]
    assert re.fullmatch(r"[0-9a-f]{16}", result["span_id"])


def test_sampled_flag_preserved():
    assert run_chain(sampled=True)[0]["sampled"] is True
    assert run_chain(sampled=False)[0]["sampled"] is False


def test_tracestate_forwarded():
    result, _ = run_chain(sampled=True, tracestate="rojo=00f067aa0ba902b7,congo=t61rcWkgMzE")
    assert result["tracestate"] == "rojo=00f067aa0ba902b7,congo=t61rcWkgMzE"


def test_tracestate_absent_stays_absent():
    result, _ = run_chain(sampled=True)
    assert result["tracestate"] is None


def test_outgoing_header_format_valid():
    seen: dict = {}

    def spy(headers: dict) -> dict:
        seen.update(headers)
        return {"trace_id": "x", "parent_span_id": "y", "span_id": "z",
                "sampled": True, "tracestate": headers.get("tracestate")}

    trace_id = secrets.token_hex(16)
    service_a.handle_incoming(
        {"traceparent": format_traceparent(trace_id, secrets.token_hex(8), True),
         "tracestate": "a=1"},
        downstream=spy,
    )
    m = TRACEPARENT_RE.match(seen["traceparent"])
    assert m, f"invalid traceparent: {seen.get('traceparent')!r}"
    assert m.group(1) == trace_id
    assert m.group(3) == "01"
    assert seen["tracestate"] == "a=1"
