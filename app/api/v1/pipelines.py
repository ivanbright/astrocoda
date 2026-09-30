"""Pipeline orchestration endpoints.

The API is intentionally thin: authenticate, authorise, persist, enqueue,
respond.  All OpenAI and Qdrant work happens inside the ARQ worker, which is
what keeps p99 HTTP latency in the millisecond range regardless of document
size.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import uuid4

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.v1.auth import API_KEY_HEADER, CurrentUser, create_access_token
from app.core.config import settings
from app.database.db import (
    PipelineRun,
    PipelineRunStatus,
    SessionDependency,
    count_pipeline_chunks,
    get_pipeline_run_for_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipelines", tags=["Pipelines"])

#: Name of the ARQ task. Referenced as a string so the API process never
#: imports the worker module (and therefore never loads the OpenAI client).
PIPELINE_TASK_NAME: str = "process_ai_pipeline_task"
QUEUE_NAME: str = "astrocoda:pipeline"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class PipelineTriggerRequest(BaseModel):
    """Payload accepted by ``POST /api/v1/pipelines/trigger``."""

    text: str = Field(
        ...,
        min_length=1,
        max_length=settings.MAX_DOCUMENT_CHARACTERS,
        description="Raw text to analyse. Chunked server side, never in the request handler.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional free-form metadata stored with the run for your own bookkeeping.",
    )


class PipelineTriggerResponse(BaseModel):
    """``202 Accepted`` body returned the moment the job is queued."""

    job_id: str = Field(description="ARQ job identifier. Use it to poll /pipelines/jobs/{job_id}.")
    status: str = Field(default=PipelineRunStatus.QUEUED)
    accepted_at: datetime
    message: str


class PipelineJobResponse(BaseModel):
    """Execution log view of a background job."""

    job_id: str
    status: str
    chunk_count: int
    processed_chunks: int
    persisted_chunks: int
    total_characters: int
    duration_ms: int | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class PipelineJobListResponse(BaseModel):
    """Paginated execution log for the authenticated consumer."""

    total: int
    jobs: list[PipelineJobResponse]


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
async def get_arq_pool(request: Request) -> ArqRedis:
    """Return the lifespan-managed ARQ Redis pool."""
    pool: ArqRedis | None = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task queue is unavailable. Please retry in a moment.",
        )
    return pool


ArqPool = Annotated[ArqRedis, Depends(get_arq_pool)]


def _to_job_response(run: PipelineRun, persisted: int) -> PipelineJobResponse:
    return PipelineJobResponse(
        job_id=run.job_id,
        status=run.status,
        chunk_count=run.chunk_count,
        processed_chunks=run.processed_chunks,
        persisted_chunks=persisted,
        total_characters=run.total_characters,
        duration_ms=run.duration_ms,
        error_message=run.error_message,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/trigger",
    response_model=PipelineTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a document for AI processing",
    description=(
        "Validates the caller's subscription, writes a `queued` execution log row and "
        "pushes the payload onto Redis. Returns immediately with a `job_id`; the ARQ "
        "worker performs the chunking, embedding and structured extraction."
    ),
)
async def trigger_pipeline(
    payload: PipelineTriggerRequest,
    user: CurrentUser,
    pool: ArqPool,
    session: SessionDependency,
) -> PipelineTriggerResponse:
    """Offload an AI pipeline run to the background worker queue."""
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive subscription. Please activate billing.",
        )

    job_id = str(uuid4())
    try:
        job = await pool.enqueue_job(
            PIPELINE_TASK_NAME,
            str(user.id),
            payload.text,
            job_id,
            _job_id=job_id,
            _queue_name=QUEUE_NAME,
        )
    except Exception as exc:  # noqa: BLE001 - logged here, opaque message to the caller
        logger.exception("Failed to enqueue pipeline job %s", job_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task queue is unavailable. Please retry in a moment.",
        ) from exc

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A job with this identifier already exists.",
        )

    run = PipelineRun(
        job_id=job_id,
        user_id=user.id,
        status=PipelineRunStatus.QUEUED,
        total_characters=len(payload.text),
    )
    session.add(run)
    await session.commit()

    logger.info(
        "Queued pipeline %s for user %s (%d characters)",
        job_id,
        user.id,
        len(payload.text),
    )
    return PipelineTriggerResponse(
        job_id=job_id,
        status=PipelineRunStatus.QUEUED,
        accepted_at=run.created_at,
        message="Job accepted and queued for background processing.",
    )


@router.get(
    "/jobs/{job_id}",
    response_model=PipelineJobResponse,
    summary="Inspect a single job",
    description="Returns the execution log for a job owned by the authenticated consumer.",
)
async def get_pipeline_job(
    user: CurrentUser,
    session: SessionDependency,
    job_id: Annotated[str, Path(min_length=8, max_length=64, description="Job id from /trigger.")],
) -> PipelineJobResponse:
    """Return the current state of a previously queued job."""
    run = await get_pipeline_run_for_user(session, job_id, user.id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found for this account.",
        )
    return _to_job_response(run, await count_pipeline_chunks(session, run.id))


@router.get(
    "/jobs",
    response_model=PipelineJobListResponse,
    summary="List recent jobs",
    description="Most recent pipeline runs for the authenticated consumer, newest first.",
)
async def list_pipeline_jobs(
    user: CurrentUser,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PipelineJobListResponse:
    """Return a paginated execution log for the authenticated consumer."""
    base = select(PipelineRun).where(PipelineRun.user_id == user.id)
    result = await session.execute(
        base.order_by(PipelineRun.created_at.desc()).limit(limit).offset(offset)
    )
    runs = list(result.scalars().all())

    jobs: list[PipelineJobResponse] = []
    for run in runs:
        jobs.append(_to_job_response(run, await count_pipeline_chunks(session, run.id)))

    total_result = await session.execute(
        select(func.count()).select_from(PipelineRun).where(PipelineRun.user_id == user.id)
    )
    return PipelineJobListResponse(total=int(total_result.scalar_one()), jobs=jobs)


@router.post(
    "/token",
    status_code=status.HTTP_200_OK,
    summary="Mint a short lived access token",
    description=(
        "Exchanges the account API key for a short lived JWT, so browser clients never "
        "have to embed a long lived secret."
    ),
)
async def issue_access_token(user: CurrentUser) -> dict[str, Any]:
    """Issue a JWT access token scoped to the authenticated consumer."""
    token, expires_in = create_access_token(user)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "header": API_KEY_HEADER,
        "is_active": user.is_active,
    }


__all__ = [
    "ArqPool",
    "PIPELINE_TASK_NAME",
    "PipelineJobListResponse",
    "PipelineJobResponse",
    "PipelineTriggerRequest",
    "PipelineTriggerResponse",
    "get_arq_pool",
    "list_pipeline_jobs",
    "router",
    "trigger_pipeline",
]
