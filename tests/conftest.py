"""Shared fixtures for the Astrocoda test suite.

The suite is hermetic: it never touches Postgres, Redis, Qdrant or the network.
The FastAPI app is exercised through ``TestClient`` *without* entering its
lifespan (so the DB / vector / ARQ startup hooks are not run), and every
external dependency the routes touch is replaced with a fake via
``app.dependency_overrides``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# `app.core.config` builds `Settings` at import time and six of its fields are
# required, so importing anything from `app` raises on a machine with no .env.
# Seed throwaway values first so `git clone && pytest` works with no setup.
#
# setdefault, never assignment: a value already exported in the environment
# (CI, a container) always wins. These strings are never transmitted anywhere,
# because the fixtures below replace every external dependency.
for _var, _value in {
    "POSTGRES_URI": "postgresql+asyncpg://astrocoda:astrocoda@127.0.0.1:5432/astrocoda",
    "REDIS_URI": "redis://127.0.0.1:6379",
    "OPENAI_API_KEY": "test-key-never-sent",
    "QDRANT_URL": "http://127.0.0.1:6333",
    "STRIPE_WEBHOOK_SECRET": "whsec_test_never_used",
    "SECRET_KEY": "test-secret-key-never-used-0123456789abcdef",
}.items():
    os.environ.setdefault(_var, _value)

from app.api.v1.auth import get_current_user
from app.api.v1.pipelines import get_arq_pool
from app.database.db import User, get_db_session
from app.main import app


class FakeArqPool:
    """Stands in for the ARQ Redis pool exposed by the app state."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.available = True

    async def enqueue_job(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        if not self.available:
            return None
        return object()


class FakeSession:
    """In-memory session: records ``add()`` calls, refuses real reads.

    Commits are no-ops so queued ``PipelineRun`` rows can be inspected in memory.
    Any unexpected read (``execute``) fails loudly rather than touching a DB.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        pass

    async def execute(self, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("Unexpected execute in fake session")


def make_user(is_active: bool = True) -> User:
    return User(
        id=uuid4(),
        email="verify@example.com",
        api_key="astro_verify_key_0123456789abcdef0123456789",
        is_active=is_active,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def arq_pool() -> FakeArqPool:
    return FakeArqPool()


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def client() -> TestClient:
    """Real FastAPI app without its lifespan (no DB / Redis / Qdrant / ARQ)."""
    test_client = TestClient(app)
    yield test_client
    test_client.close()


@pytest.fixture(autouse=True)
def _install_service_overrides(arq_pool: FakeArqPool, fake_session: FakeSession) -> None:
    """Point the app's DB and queue dependencies at the fakes for every test."""

    async def fake_db_session() -> AsyncIterator[FakeSession]:
        async with fake_session:
            yield fake_session

    app.dependency_overrides[get_db_session] = fake_db_session
    app.dependency_overrides[get_arq_pool] = lambda: arq_pool
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def set_current_user(client: TestClient) -> None:
    """Fixture factory: override ``get_current_user`` for one test."""

    def _set(*, active: bool = True) -> User:
        user = make_user(active)
        app.dependency_overrides[get_current_user] = lambda: user
        return user

    return _set