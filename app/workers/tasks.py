"""ARQ background worker.

The API process never performs AI work inline.  It validates, persists a
``PipelineRun`` row and hands the payload to Redis, returning ``202 Accepted``
immediately.  This module is the consumer of that queue:

    chunk -> embed (OpenAI) -> extract (OpenAI + Instructor) -> Qdrant + Postgres

The job is built to be replay safe.  Each chunk is committed the moment it is
durable, an interrupted run resumes at the first chunk that is missing, and the
``(run_id, chunk_index)`` unique index guarantees a retry can never duplicate a
row or a vector.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import instructor
from arq.connections import RedisSettings
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.database.db import (
    PipelineChunk,
    PipelineRun,
    PipelineRunStatus,
    async_session_factory,
    close_db,
    create_db_and_tables,
    get_pipeline_run,
    utcnow,
)
from app.database.vector import close_vector_db, init_vector_db, upsert_document_vector
from app.workers.schemas import ExtractedInsight

logger = logging.getLogger("astrocoda.worker")

__all__ = ["WorkerSettings", "chunk_text", "process_ai_pipeline_task", "startup", "shutdown"]

MAX_ERROR_LENGTH = 2000


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------
def chunk_text(text: str, chunk_size: int) -> list[str]:
    """Split ``text`` into chunks of at most ``chunk_size`` characters.

    Windows are cut at the nearest whitespace in the last 40% of the window, so
    words are never broken in half while forward progress is still guaranteed.
    """
    normalised = text.strip()
    if not normalised:
        return []

    chunks: list[str] = []
    start = 0
    length = len(normalised)
    floor = int(chunk_size * 0.6)

    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            boundary = normalised.rfind(" ", start + floor, end)
            if boundary > start:
                end = boundary
        piece = normalised[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end

    return chunks


# ---------------------------------------------------------------------------
# AI helpers
# ---------------------------------------------------------------------------
async def embed_batch(openai_client: AsyncOpenAI, batch: list[str]) -> list[list[float]]:
    """Embed a batch of chunks, returning vectors in the same order."""
    if not batch:
        return []
    response = await openai_client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=batch,
        encoding_format="float",
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    return [item.embedding for item in ordered]


async def iter_embedded_chunks(
    openai_client: AsyncOpenAI,
    chunks: list[str],
    window: int,
    skip: set[int] | None = None,
) -> AsyncIterator[tuple[int, str, list[float]]]:
    """Yield ``(index, chunk, vector)`` while embedding a sliding window.

    Batching amortises the HTTP round trip across up to ``window`` chunks while
    keeping peak memory proportional to the window rather than the document.
    Indices listed in ``skip`` are never embedded, so resuming a partially
    processed run does not re-pay for tokens it already spent.
    """
    skip = skip or set()
    for start in range(0, len(chunks), window):
        pending = [
            (index, chunk)
            for index, chunk in enumerate(chunks[start : start + window], start=start)
            if index not in skip
        ]
        if not pending:
            continue
        vectors = await embed_batch(openai_client, [chunk for _, chunk in pending])
        for (index, chunk), vector in zip(pending, vectors):
            yield index, chunk, vector


async def extract_insight(
    instructor_client: instructor.AsyncInstructor,
    chunk: str,
) -> ExtractedInsight:
    """Force the LLM to answer with a validated ``ExtractedInsight`` object.

    Instructor retries the call whenever the model returns malformed JSON or
    omits a field, so this either returns a typed model or raises - it can never
    return a loosely typed string.
    """
    insight: ExtractedInsight = await instructor_client.chat.completions.create(
        model=settings.OPENAI_CHAT_MODEL,
        response_model=ExtractedInsight,
        max_retries=settings.INSTRUCTOR_MAX_RETRIES,
        max_tokens=settings.LLM_MAX_TOKENS,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a precise data extraction engine. You never chat and never "
                    "add commentary; you return only structured data conforming to the "
                    "supplied schema, derived strictly from the provided passage."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Analyse the passage delimited by <passage> tags and produce a JSON "
                    "object with these fields:\n"
                    "- summary: a dense two to three sentence summary of the passage.\n"
                    "- sentiment: the overall sentiment, exactly one of positive, neutral "
                    "or negative.\n"
                    "- keywords: between three and eight short topical keywords.\n\n"
                    f"<passage>\n{chunk}\n</passage>"
                ),
            },
        ],
    )
    return insight


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
async def _resolve_run(
    session: AsyncSession,
    *,
    job_id: str,
    user_id: UUID,
    total_characters: int,
    chunk_count: int,
) -> PipelineRun:
    """Fetch the run created by the API, or recreate it if it was lost."""
    run = await get_pipeline_run(session, job_id)
    if run is None:
        run = PipelineRun(
            job_id=job_id,
            user_id=user_id,
            status=PipelineRunStatus.QUEUED,
            total_characters=total_characters,
            chunk_count=chunk_count,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        logger.info("Recreated missing pipeline run %s during replay", job_id)
        return run

    run.chunk_count = chunk_count
    run.total_characters = total_characters
    await session.commit()
    return run


async def _completed_chunk_indices(session: AsyncSession, run: PipelineRun) -> set[int]:
    """Chunk indices already durable in PostgreSQL, so a replay can skip them."""
    result = await session.execute(
        select(PipelineChunk.chunk_index).where(PipelineChunk.run_id == run.id)
    )
    return {int(index) for index in result.scalars().all()}


async def _persist_chunk(
    session: AsyncSession,
    *,
    run: PipelineRun,
    user_id: UUID,
    chunk_index: int,
    insight: ExtractedInsight,
    vector: list[float],
) -> None:
    """Write the vector to Qdrant, then mirror the extraction into PostgreSQL."""
    point_id = str(uuid4())
    payload: dict[str, Any] = {
        "job_id": run.job_id,
        "run_id": str(run.id),
        "user_id": str(user_id),
        "chunk_index": chunk_index,
        "summary": insight.summary,
        "sentiment": insight.sentiment,
        "keywords": insight.keywords,
        "model": settings.OPENAI_CHAT_MODEL,
        "embedded_at": datetime.now(timezone.utc).isoformat(),
    }

    await upsert_document_vector(point_id, vector, payload)

    session.add(
        PipelineChunk(
            run_id=run.id,
            user_id=user_id,
            chunk_index=chunk_index,
            vector_point_id=point_id,
            summary=insight.summary,
            sentiment=insight.sentiment.strip().lower()[:32],
            keywords=list(insight.keywords),
        )
    )
    run.processed_chunks = chunk_index + 1
    await session.commit()


async def _mark_failed(session: AsyncSession, job_id: str, error: str) -> None:
    """Record the failure on a freshly loaded run.

    The caller has already rolled back, which expires every loaded attribute,
    so the row is re-read instead of reusing a stale ORM object.
    """
    run = await get_pipeline_run(session, job_id)
    if run is None:
        return
    run.status = PipelineRunStatus.FAILED
    run.error_message = error[:MAX_ERROR_LENGTH]
    run.completed_at = utcnow()
    await session.commit()


async def _finalise(session: AsyncSession, run: PipelineRun, duration_ms: int) -> None:
    run.status = PipelineRunStatus.COMPLETED
    run.completed_at = utcnow()
    run.duration_ms = duration_ms
    run.processed_chunks = run.chunk_count
    await session.commit()


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------
async def process_ai_pipeline_task(
    ctx: dict[str, Any],
    user_id: str,
    raw_text: str,
    job_id: str = "",
) -> dict[str, Any]:
    """Chunk, embed and structure a document for one consumer.

    Args:
        ctx: ARQ context populated by :func:`startup`.
        user_id: UUID string of the owning :class:`~app.database.db.User`.
        raw_text: The document submitted through the trigger endpoint.
        job_id: ARQ job id. Optional so the task can be invoked directly, but
            the API always supplies it so the run row can be correlated and a
            replay can resume.

    Returns:
        A small summary dict, which ARQ stores as the job result.
    """
    started = time.perf_counter()
    resolved_job_id = job_id or str(uuid4())
    owner_id = UUID(user_id)
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    openai_client: AsyncOpenAI = ctx["openai_client"]
    instructor_client: instructor.AsyncInstructor = ctx["instructor_client"]

    chunks = chunk_text(raw_text, settings.CHUNK_SIZE)
    logger.info(
        "Pipeline %s started for user %s (%d characters -> %d chunks)",
        resolved_job_id,
        user_id,
        len(raw_text),
        len(chunks),
    )

    if not chunks:
        async with session_factory() as session:
            run = await _resolve_run(
                session,
                job_id=resolved_job_id,
                user_id=owner_id,
                total_characters=0,
                chunk_count=0,
            )
            await _finalise(session, run, int((time.perf_counter() - started) * 1000))
        logger.warning("Pipeline %s closed immediately: the document was empty", resolved_job_id)
        return {
            "job_id": resolved_job_id,
            "chunks": 0,
            "status": PipelineRunStatus.COMPLETED,
        }

    async with session_factory() as session:
        run = await _resolve_run(
            session,
            job_id=resolved_job_id,
            user_id=owner_id,
            total_characters=len(raw_text),
            chunk_count=len(chunks),
        )

        already_done = await _completed_chunk_indices(session, run)
        if already_done:
            logger.info(
                "Pipeline %s resuming, %d/%d chunks already durable",
                resolved_job_id,
                len(already_done),
                len(chunks),
            )

        run.status = PipelineRunStatus.PROCESSING
        run.started_at = run.started_at or utcnow()
        run.completed_at = None
        run.error_message = None
        run.processed_chunks = len(already_done)
        await session.commit()

        window = max(1, settings.EMBEDDING_BATCH_SIZE)
        try:
            async for index, chunk, vector in iter_embedded_chunks(
                openai_client, chunks, window, already_done
            ):
                insight = await extract_insight(instructor_client, chunk)
                await _persist_chunk(
                    session,
                    run=run,
                    user_id=owner_id,
                    chunk_index=index,
                    insight=insight,
                    vector=vector,
                )
                logger.info(
                    "Pipeline %s chunk %d/%d persisted",
                    resolved_job_id,
                    index + 1,
                    len(chunks),
                )

            await _finalise(session, run, int((time.perf_counter() - started) * 1000))

        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised for ARQ retry
            await session.rollback()
            logger.exception("Pipeline %s failed on attempt", resolved_job_id)
            await _mark_failed(session, resolved_job_id, f"{type(exc).__name__}: {exc}")
            raise

    logger.info(
        "Pipeline %s completed %d chunks in %d ms",
        resolved_job_id,
        len(chunks),
        int((time.perf_counter() - started) * 1000),
    )
    return {
        "job_id": resolved_job_id,
        "chunks": len(chunks),
        "status": PipelineRunStatus.COMPLETED,
    }


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------
async def startup(ctx: dict[str, Any]) -> None:
    """Open every long lived resource once per worker process."""
    ctx["settings"] = settings
    ctx["session_factory"] = async_session_factory
    ctx["openai_client"] = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL or "https://api.openai.com/v1",
        max_retries=3,
        timeout=60.0,
    )
    ctx["instructor_client"] = instructor.from_openai(
        ctx["openai_client"],
        mode=instructor.Mode.TOOLS,
    )

    await create_db_and_tables()
    await init_vector_db()
    logger.info(
        "Astrocoda worker ready (provider=%s, chat=%s, embeddings=%s, collection=%s, chunk=%d)",
        settings.OPENAI_BASE_URL or "https://api.openai.com/v1",
        settings.OPENAI_CHAT_MODEL,
        settings.OPENAI_EMBEDDING_MODEL,
        settings.VECTOR_COLLECTION_NAME,
        settings.CHUNK_SIZE,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    """Release the OpenAI, Qdrant and PostgreSQL resources."""
    openai_client: AsyncOpenAI | None = ctx.get("openai_client")
    if openai_client is not None:
        await openai_client.close()
    await close_vector_db()
    await close_db()
    logger.info("Astrocoda worker stopped cleanly")


class WorkerSettings:
    """ARQ entrypoint: ``arq app.workers.tasks.WorkerSettings``."""

    functions = [process_ai_pipeline_task]

    on_startup = startup
    on_shutdown = shutdown

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URI)

    queue_name = "astrocoda:pipeline"
    job_timeout = settings.ARQ_JOB_TIMEOUT
    max_tries = settings.ARQ_MAX_TRIES
    keep_result = 3600
    health_check_interval = 30
