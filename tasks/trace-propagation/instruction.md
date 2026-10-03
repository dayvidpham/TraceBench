# Fix distributed-trace propagation (W3C Trace Context)

Two services live in `/app`:

| File | Status |
| - | - |
| `tracing.py` | Helpers (`parse_traceparent`, `format_traceparent`). Correct. |
| `service_b.py` | Downstream service. Correct. |
| `service_a.py` | Upstream service. **Contains the bug in `call_downstream`.** |

## Symptom

Traces break at the service A → service B hop: the downstream span lands in a
different trace, sampling decisions are lost, and `tracestate` is dropped.
Dashboards show disconnected fragments instead of one end-to-end trace.

## Your task

Fix `call_downstream` in `/app/service_a.py` so the outgoing call:

1. Propagates the incoming `trace_id` **unchanged** (never mint a new one).
2. Uses a fresh `span_id` for the outgoing call (the `span_id` argument is
   minted fresh per request — use it; never reuse the incoming span or emit
   all-zero).
3. Preserves the incoming `sampled` flag (`01` stays `01`, `00` stays `00`).
4. Forwards `tracestate` unchanged when present (omit the header when absent).
5. Emits a valid W3C `traceparent`: `00-<32-hex-trace-id>-<16-hex-span-id>-<02-flag>`.

Only `service_a.py` needs to change. Do not modify `tracing.py` or `service_b.py`.

## Constraints

* No network access during this task — aiming for offline `pytest` only.
* Do not hardcode trace ids; grading uses randomized ids.

## Check locally

```bash
python3 -m pytest /tests/test_outputs.py -v   # inside the sandbox (cwd /app)
# or, if iterating on the image contents locally:
python3 -m pytest tests/test_outputs.py -v     # with PYTHONPATH pointing at environment/app
```
