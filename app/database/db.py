"""PostgreSQL persistence layer.

Owns the async SQLModel engine, the ORM models used by both the API process
and the ARQ worker, and the FastAPI session dependency.  Both processes import
this module, so the connection pool is configured exactly once per process.
"""

import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Annotated, Any, Optional
from uuid import UUID, uuid4

from fastapi import Depends
from sqlalchemy import JSON, Column, DateTime, Index, func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import Field, Relationship, SQLModel, select

from app.core.config import settings

__all__ = [
    "PipelineChunk",
    "PipelineRun",
    "PipelineRunStatus",
    "SessionDependency",
    "User",
    "async_session_factory",
    "build_user",
    "close_db",
    "count_pipeline_chunks",
    "create_db_and_tables",
    "engine",
    "generate_api_key",
    "get_db_session",
    "get_pipeline_run",
    "get_pipeline_run_for_user",
    "get_user_by_api_key",
    "get_user_by_email",
    "get_user_by_id",
    "get_user_by_stripe_customer_id",
    "normalise_email",
    "ping_db",
    "utcnow",
]

# ---------------------------------------------------------------------------
# Engine / session factory
# ---------------------------------------------------------------------------
engine = create_async_engine(
    settings.POSTGRES_URI,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def utcnow() -> datetime:
    """Timezone aware UTC timestamp, the single clock used by the ORM layer."""
    return datetime.now(timezone.utc)


def generate_api_key(prefix: str | None = None) -> str:
    """Mint a URL safe, high entropy API key for a consumer application."""
    return f"{prefix or settings.API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


class PipelineRunStatus:
    """Canonical lifecycle values for :class:`PipelineRun.status`."""

    QUEUED: str = "queued"
    PROCESSING: str = "processing"
    COMPLETED: str = "completed"
    FAILED: str = "failed"

    ALL: tuple[str, ...] = (QUEUED, PROCESSING, COMPLETED, FAILED)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class User(SQLModel, table=True):
    """A paying consumer of the Astrocoda pipeline."""

    __tablename__ = "users"
    __table_args__ = (Index("ix_users_created_at", "created_at"),)

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    email: str = Field(unique=True, index=True, max_length=320)
    stripe_customer_id: str | None = Field(
        default=None,
        unique=True,
        index=True,
        max_length=255,
    )
    is_active: bool = Field(
        default=False,
        index=True,
        description="Gated by Stripe. False means pipeline access is denied.",
    )
    api_key: str = Field(unique=True, index=True, min_length=32, max_length=255)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    runs: Optional[list["PipelineRun"]] = Relationship(
        back_populates="user",
        cascade_delete=True,
        sa_relationship_kwargs={"lazy": "selectin"},
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<User id={self.id} email={self.email!r} active={self.is_active}>"


class PipelineRun(SQLModel, table=True):
    """Execution log for a single background pipeline job."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (Index("ix_pipeline_runs_user_created", "user_id", "created_at"),)

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    job_id: str = Field(unique=True, index=True, max_length=64, description="ARQ job identifier.")
    user_id: UUID = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    status: str = Field(default=PipelineRunStatus.QUEUED, index=True, max_length=32)
    chunk_count: int = Field(default=0, description="Chunks discovered by the worker.")
    processed_chunks: int = Field(default=0, description="Chunks fully persisted.")
    total_characters: int = Field(default=0, description="Size of the submitted raw text.")
    duration_ms: int | None = Field(default=None, description="Wall clock time of the worker loop.")
    error_message: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)

    user: Optional["User"] = Relationship(back_populates="runs")
    chunks: Optional[list["PipelineChunk"]] = Relationship(
        back_populates="run",
        cascade_delete=True,
        sa_relationship_kwargs={"lazy": "selectin"},
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<PipelineRun job_id={self.job_id!r} status={self.status!r}>"


class PipelineChunk(SQLModel, table=True):
    """Structured extraction of a single chunk, cached in PostgreSQL.

    Qdrant holds the vectors; this table is the queryable, human readable
    mirror of what the LLM extracted.
    """

    __tablename__ = "pipeline_chunks"
    __table_args__ = (Index("ix_pipeline_chunks_run_index", "run_id", "chunk_index", unique=True),)

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    run_id: UUID = Field(foreign_key="pipeline_runs.id", ondelete="CASCADE")
    user_id: UUID = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    chunk_index: int = Field(description="Zero based position inside the document.")
    vector_point_id: str = Field(index=True, max_length=64, description="Qdrant point id.")
    summary: str = Field(description="LLM generated summary of the chunk.")
    sentiment: str = Field(max_length=32, description="positive | neutral | negative")
    keywords: list[str] = Field(
        default_factory=list,
        sa_type=JSON,
        description="Topical keywords extracted from the chunk.",
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    run: Optional["PipelineRun"] = Relationship(back_populates="chunks")

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<PipelineChunk run_id={self.run_id} index={self.chunk_index}>"


# ---------------------------------------------------------------------------
# Schema bootstrap / teardown
# ---------------------------------------------------------------------------
async def create_db_and_tables() -> None:
    """Create every table declared on ``SQLModel.metadata`` if it is missing."""
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


async def close_db() -> None:
    """Dispose of the connection pool. Safe to call more than once."""
    await engine.dispose()


async def ping_db() -> bool:
    """Return True when PostgreSQL answers a trivial round trip."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - health endpoint best effort
        return False


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request scoped session.

    The session is rolled back on any unhandled error so a failed request can
    never leave a poisoned transaction behind for the next request.
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


#: Typed alias so route signatures stay short and self documenting.
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Reusable queries
# ---------------------------------------------------------------------------
async def get_user_by_api_key(session: AsyncSession, api_key: str) -> User | None:
    result = await session.execute(select(User).where(User.api_key == api_key))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    normalised = email.strip().lower()
    result = await session.execute(select(User).where(User.email == normalised))
    return result.scalar_one_or_none()


async def get_user_by_stripe_customer_id(session: AsyncSession, customer_id: str) -> User | None:
    result = await session.execute(
        select(User).where(User.stripe_customer_id == customer_id)
    )
    return result.scalar_one_or_none()


async def get_pipeline_run(session: AsyncSession, job_id: str) -> PipelineRun | None:
    result = await session.execute(
        select(PipelineRun).where(PipelineRun.job_id == job_id)
    )
    return result.scalar_one_or_none()


async def get_pipeline_run_for_user(
    session: AsyncSession, job_id: str, user_id: UUID
) -> PipelineRun | None:
    result = await session.execute(
        select(PipelineRun).where(PipelineRun.job_id == job_id, PipelineRun.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def count_pipeline_chunks(session: AsyncSession, run_id: UUID) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count()).select_from(PipelineChunk).where(PipelineChunk.run_id == run_id)
    )
    return int(result.scalar_one())


def normalise_email(email: str) -> str:
    """Stripe and manual entries must land on the same row."""
    return email.strip().lower()


def build_user(
    *,
    email: str,
    api_key: str | None = None,
    stripe_customer_id: str | None = None,
    is_active: bool = False,
    extra: dict[str, Any] | None = None,
) -> User:
    """Factory used by the webhook and any future onboarding flow."""
    return User(
        email=normalise_email(email),
        api_key=api_key or generate_api_key(),
        stripe_customer_id=stripe_customer_id,
        is_active=is_active,
        **(extra or {}),
    )
