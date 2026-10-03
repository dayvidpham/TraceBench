FROM python:3.12-slim

# Bake test deps into the image so agent + verifier phases can run with
# no network access (see task.toml network policy).
RUN pip install --no-cache-dir "pytest==8.4.1"

WORKDIR /app
COPY app /app

# Sanity check: buggy service must import cleanly.
RUN python3 -c "import service_a, service_b, tracing; print('env ok')"
