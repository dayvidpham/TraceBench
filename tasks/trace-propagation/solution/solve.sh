#!/bin/bash
# Oracle solution: fix call_downstream to propagate trace context per W3C.
set -euo pipefail

python3 - <<'EOF'
from pathlib import Path

path = Path("/app/service_a.py")
src = path.read_text()

old = '''def call_downstream(trace_id: str, span_id: str, sampled: bool,
                    tracestate: str | None, downstream) -> dict:
    # BUG: mints a fresh trace-id, resets the sampled flag, and drops
    # tracestate — breaking distributed-trace correlation.
    fresh_trace = secrets.token_hex(16)
    headers = {"traceparent": format_traceparent(fresh_trace, new_span_id(), False)}
    return downstream(headers)'''

new = '''def call_downstream(trace_id: str, span_id: str, sampled: bool,
                    tracestate: str | None, downstream) -> dict:
    # span_id is freshly minted per request by handle_incoming: use it as
    # the outgoing span (never reuse the incoming span, never all-zero).
    headers = {"traceparent": format_traceparent(trace_id, span_id, sampled)}
    if tracestate is not None:
        headers["tracestate"] = tracestate
    return downstream(headers)'''

assert old in src, "expected buggy block not found"
path.write_text(src.replace(old, new))
print("solution applied")
EOF
