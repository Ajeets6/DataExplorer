FROM ghcr.io/astral-sh/uv:0.8-python3.11-bookworm-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.11-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATAEXPLORER_ENVIRONMENT=production
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src
COPY --chown=app:app migrations ./migrations
USER app
EXPOSE 8080
CMD ["uvicorn", "dataexplorer.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
