"""Guards around vector persistence."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.core.config import settings
from app.database.vector import upsert_document_vector


def test_wrong_dimension_rejected_before_network():
    with pytest.raises(ValueError, match=f"{settings.EMBEDDING_DIMENSIONS}"):
        asyncio.run(upsert_document_vector(str(uuid4()), [0.1] * 128, {}))