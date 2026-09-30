"""Qdrant vector store lifecycle and helpers.

The client is created lazily and cached at module scope.  Both the API process
and the ARQ worker own exactly one client each, which keeps the HTTP/2
connection pool warm for the whole lifetime of the process.
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "COLLECTION_NAME",
    "close_vector_db",
    "get_vector_db",
    "init_vector_db",
    "ping_vector_db",
    "upsert_document_vector",
]

COLLECTION_NAME: str = settings.VECTOR_COLLECTION_NAME
VECTOR_SIZE: int = settings.EMBEDDING_DIMENSIONS

_client: AsyncQdrantClient | None = None


async def get_vector_db() -> AsyncQdrantClient:
    """Return the process wide Qdrant client, creating it on first use."""
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
            prefer_grpc=True,
            timeout=30.0,
        )
        logger.debug("Initialised async Qdrant client for %s", settings.QDRANT_URL)
    return _client


async def init_vector_db() -> None:
    """Verify the chunk collection exists, creating it when it does not.

    Called from the FastAPI lifespan and from the ARQ worker startup hook, so
    the operation must stay idempotent.
    """
    client = await get_vector_db()
    if await client.collection_exists(collection_name=COLLECTION_NAME):
        logger.info("Qdrant collection %r already present", COLLECTION_NAME)
        return

    await client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(
            size=VECTOR_SIZE,
            distance=models.Distance.COSINE,
        ),
    )
    logger.info(
        "Created Qdrant collection %r (size=%d, distance=cosine)",
        COLLECTION_NAME,
        VECTOR_SIZE,
    )


async def upsert_document_vector(
    chunk_id: str,
    vector: list[float],
    payload: dict[str, Any],
) -> None:
    """Insert or replace a single embedded chunk in Qdrant.

    Args:
        chunk_id: UUID string used as the Qdrant point id, which makes the
            write idempotent across ARQ retries.
        vector: Dense embedding. Its length must equal ``settings.EMBEDDING_DIMENSIONS``.
        payload: Filterable metadata stored alongside the vector.
    """
    if len(vector) != VECTOR_SIZE:
        raise ValueError(
            f"Embedding dimension mismatch: expected {VECTOR_SIZE}, received {len(vector)}."
        )

    client = await get_vector_db()
    await client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            models.PointStruct(
                id=chunk_id,
                vector=vector,
                payload=payload,
            )
        ],
    )


async def upsert_document_vectors(
    points: list[tuple[str, list[float], dict[str, Any]]],
) -> None:
    """Batch variant of :func:`upsert_document_vector`."""
    if not points:
        return
    client = await get_vector_db()
    await client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            models.PointStruct(
                id=chunk_id,
                vector=vector,
                payload=payload,
            )
            for chunk_id, vector, payload in points
        ],
    )


async def ping_vector_db() -> bool:
    """Return True when Qdrant answers a collection lookup."""
    try:
        client = await get_vector_db()
        await client.collection_exists(collection_name=COLLECTION_NAME)
        return True
    except Exception:  # pragma: no cover - health endpoint best effort
        return False


async def close_vector_db() -> None:
    """Close the pooled HTTP connections held by the client."""
    global _client
    if _client is None:
        return
    await _client.close()
    _client = None
    logger.debug("Closed Qdrant client")
