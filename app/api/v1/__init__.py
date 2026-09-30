"""Versioned HTTP API package (v1).

Both routers are mounted onto a single aggregate router so ``app.main`` only
ever has to know about one object.
"""

from fastapi import APIRouter

from app.api.v1 import pipelines, webhooks

api_router = APIRouter()
api_router.include_router(pipelines.router)
api_router.include_router(webhooks.router)

__all__ = ["api_router", "pipelines", "webhooks"]
