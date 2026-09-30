"""Unit tests for the whitespace-aware text chunker."""

from __future__ import annotations

from app.workers.tasks import chunk_text


def test_splits_long_document_into_multiple_chunks():
    chunks = chunk_text("word " * 900, 1000)
    assert len(chunks) > 4
    assert all(len(c) <= 1000 for c in chunks)
    assert all(not c.endswith("wor") for c in chunks)
    assert "".join(chunks).replace(" ", "") == ("word " * 900).replace(" ", "")


def test_empty_input_returns_empty_list():
    assert chunk_text("   ", 1000) == []


def test_short_input_returns_single_chunk():
    assert chunk_text("hi", 1000) == ["hi"]


def test_no_whitespace_still_splits_evenly():
    chunks = chunk_text("a" * 2500, 200)
    assert len(chunks) == 13
    assert "".join(chunks) == "a" * 2500


def test_boundary_whitespace_is_respected():
    assert chunk_text("x" * 999 + " " + "y" * 999, 1000) == ["x" * 999, "y" * 999]