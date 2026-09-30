"""Contract tests for the structured extraction schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.workers.schemas import ExtractedInsight


def test_valid_payload_parses():
    insight = ExtractedInsight(
        summary="A summary.",
        sentiment="positive",
        keywords=["a", "b", "c"],
    )
    assert insight.sentiment == "positive"
    assert len(insight.keywords) == 3


def test_too_few_keywords_rejected():
    with pytest.raises(ValidationError):
        ExtractedInsight(summary="s", sentiment="neutral", keywords=["only-one"])


def test_missing_keywords_rejected():
    with pytest.raises(ValidationError):
        ExtractedInsight(summary="s", sentiment="neutral")


def test_sentiment_must_be_at_least_three_chars():
    with pytest.raises(ValidationError):
        ExtractedInsight(summary="s", sentiment="ok", keywords=["a", "b", "c"])