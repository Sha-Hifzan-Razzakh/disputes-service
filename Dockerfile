FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

# build: resolve the locked runtime dependencies into /app/.venv
FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# runtime: patched OS packages, the venv and the code; no uv (or its bundled crates) in the shipped image
FROM python:3.12-slim
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY . .
RUN useradd --system --uid 10001 app   # owns nothing: code and venv stay read-only for the runtime user
USER app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    LANGGRAPH_STRICT_MSGPACK=true \
    APP_CHECKPOINT_AUTO_SETUP=false
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
