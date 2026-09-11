# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Production Priority & Scheduling Engine — backend image
# Multi-stage: build wheels in a full image, run in a slim non-root image.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
COPY synthetic ./synthetic
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --upgrade pip wheel \
    && pip wheel --wheel-dir /wheels ".[optimization]"

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PPSE_ENVIRONMENT=prod \
    PPSE_LOG_JSON=true

RUN groupadd --system ppse && useradd --system --gid ppse --create-home ppse

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Application code (kept for alembic + cli which run from the source tree)
COPY --chown=ppse:ppse app ./app
COPY --chown=ppse:ppse synthetic ./synthetic
COPY --chown=ppse:ppse alembic.ini ./alembic.ini
COPY --chown=ppse:ppse alembic ./alembic

USER ppse
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
