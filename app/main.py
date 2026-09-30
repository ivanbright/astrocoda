"""Astrocoda application entrypoint.

Boot order matters and is enforced by the lifespan handler:

1. PostgreSQL schema is created (SQLModel metadata) so the auth dependency can
   query immediately.
2. The Qdrant collection is verified or created so the worker never races the
   API on first write.
3. The ARQ Redis pool is opened and stashed on ``app.state`` for the trigger
   endpoint to reuse.

Shutdown reverses the order, draining pooled connections cleanly.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.database.db import close_db, create_db_and_tables, ping_db
from app.database.vector import close_vector_db, init_vector_db, ping_vector_db

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("astrocoda.api")

DESCRIPTION = """
**Astrocoda** is a production ready backend for AI data pipelines.

Send raw text to `POST /api/v1/pipelines/trigger` and receive a `job_id` in
under a millisecond. An ARQ worker chunks the document, embeds it with
OpenAI, extracts typed JSON with Instructor, and persists vectors to Qdrant
alongside a queryable record in PostgreSQL.

Authenticate every call with the `X-API-Key` header issued when a Stripe
checkout completes.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise and tear down every shared resource exactly once."""
    logger.info("Starting %s (%s)", settings.PROJECT_NAME, settings.ENVIRONMENT)

    await create_db_and_tables()
    logger.info("PostgreSQL schema verified")

    await init_vector_db()
    logger.info("Qdrant collection %r verified", settings.VECTOR_COLLECTION_NAME)

    arq_pool = cast(
        ArqRedis,
        await create_pool(RedisSettings.from_dsn(settings.REDIS_URI)),
    )
    app.state.arq_pool = arq_pool
    app.state.settings = settings
    logger.info("ARQ queue connected (%s)", settings.REDIS_URI)

    try:
        yield
    finally:
        await arq_pool.aclose()
        await close_vector_db()
        await close_db()
        logger.info("Shutdown complete, resources released")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    contact={"name": "Astrocoda", "url": "https://github.com/astrocoda/astrocoda"},
    license_info={"name": "MIT"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "Stripe-Signature"],
    expose_headers=["X-Request-ID"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace or driver message to a client."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error."},
    )


@app.get("/", tags=["System"], summary="Service metadata")
async def root() -> dict[str, str]:
    """Identify the running deployment and where to find the docs."""
    return {
        "service": settings.PROJECT_NAME,
        "version": "1.0.0",
        "environment": settings.ENVIRONMENT,
        "docs": "/docs",
        "api_prefix": settings.API_V1_PREFIX,
    }


@app.get("/health", tags=["System"], summary="Dependency health check")
async def health() -> JSONResponse:
    """Report on PostgreSQL, Redis and Qdrant connectivity."""
    arq_pool: ArqRedis | None = getattr(app.state, "arq_pool", None)
    try:
        if arq_pool is None:
            raise RuntimeError("ARQ pool is not initialised")
        await arq_pool.ping()
        redis_ok = True
    except Exception:  # pragma: no cover - health endpoint best effort
        redis_ok = False

    postgres_ok, qdrant_ok = await ping_db(), await ping_vector_db()
    healthy = postgres_ok and redis_ok and qdrant_ok

    payload = {
        "status": "healthy" if healthy else "degraded",
        "dependencies": {
            "postgres": "up" if postgres_ok else "down",
            "redis": "up" if redis_ok else "down",
            "qdrant": "up" if qdrant_ok else "down",
        },
        "version": "1.0.0",
    }
    return JSONResponse(
        status_code=(
            status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content=payload,
    )


__all__ = ["app", "lifespan"]
