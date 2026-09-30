"""Pydantic schemas shared by the worker and the API layer.

Keeping the LLM contract in one module means the extraction schema can evolve
in a single place and be imported by both processes without creating an import
cycle.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["ExtractedInsight"]


class ExtractedInsight(BaseModel):
    """Structured schema Instructor forces the LLM to satisfy.

    Field descriptions are part of the prompt: Instructor serialises the schema
    into the tool definition, so the wording here directly drives extraction
    quality.
    """

    summary: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="A dense two to three sentence summary of the passage.",
    )
    sentiment: str = Field(
        ...,
        min_length=3,
        max_length=32,
        description="Overall sentiment of the passage: positive, neutral or negative.",
    )
    keywords: list[str] = Field(
        ...,
        min_length=3,
        max_length=8,
        description="Between three and eight short topical keywords from the passage.",
    )
