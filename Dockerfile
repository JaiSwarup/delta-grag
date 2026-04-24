FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DGRAG_CACHE_DIR=/app/.cache/dgrag \
    DGRAG_HOST=0.0.0.0 \
    DGRAG_PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY dgrag.py ./
COPY scripts ./scripts

RUN mkdir -p /app/.cache/dgrag /app/src/grammar_libs \
    && chmod +x /app/scripts/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import importlib; import sys; importlib.import_module('dgrag'); print('ok')" || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "src.webhook:app", "--host", "0.0.0.0", "--port", "8000"]
