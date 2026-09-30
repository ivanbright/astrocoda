# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Astrocoda - single image, two roles.
#   web     -> uvicorn app.main:app
#   worker  -> arq app.workers.tasks.WorkerSettings
# Both roles share this image so they can never drift out of sync.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/code

WORKDIR /code

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        curl \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Dependencies are installed in their own layer so application edits do not
# invalidate the pip cache.
#
# Local Linux wheels in ./wheels are preferred so the build can run fully
# offline on flaky networks.  That directory ships empty, so the build falls
# back to the configured package index unless you populate it yourself.
# ---------------------------------------------------------------------------
FROM base AS builder

COPY requirements.txt ./
COPY wheels ./wheels
RUN python -m venv /opt/venv \
    && if ls wheels/*.whl >/dev/null 2>&1; then \
        /opt/venv/bin/pip install --no-index --find-links=./wheels -r requirements.txt; \
       else \
        /opt/venv/bin/pip install --default-timeout=120 --retries=10 -r requirements.txt; \
       fi

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM base AS runtime

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN groupadd --system astrocoda \
    && useradd --system --gid astrocoda --home-dir /code astrocoda

COPY --chown=astrocoda:astrocoda app ./app

USER astrocoda

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
