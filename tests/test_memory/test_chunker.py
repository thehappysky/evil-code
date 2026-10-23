"""Tests for memory/chunker.py — Markdown chunking."""

import pytest

from code_agent.memory.chunker import Chunk, chunk_markdown, _split_on_h2


SAMPLE_MD = """# Project Title

Intro paragraph here.

## First Section
Content of the first section.
More content.

## Second Section
Content of the second section.

### Subsection
Sub content here.

## Third Section
Final content.
"""


class TestChunkMarkdown:
    def test_splits_on_h2(self):
        chunks = chunk_markdown(SAMPLE_MD, "test.md")
        assert len(chunks) == 3  # 3 H2 sections

    def test_chunk_has_required_fields(self):
        chunks = chunk_markdown(SAMPLE_MD, "test.md")
        for chunk in chunks:
            assert chunk.id.startswith("test.md:")
            assert len(chunk.text) > 0
            assert chunk.start_line > 0
            assert chunk.end_line >= chunk.start_line
            assert len(chunk.hash) == 64  # SHA256 hex digest

    def test_chunk_hash_deterministic(self):
        chunks1 = chunk_markdown(SAMPLE_MD, "test.md")
        chunks2 = chunk_markdown(SAMPLE_MD, "test.md")
        for c1, c2 in zip(chunks1, chunks2):
            assert c1.hash == c2.hash

    def test_chunk_hash_changes_with_content(self):
        chunks1 = chunk_markdown("## A\ncontent A", "t.md")
        chunks2 = chunk_markdown("## A\ncontent B", "t.md")
        assert chunks1[0].hash != chunks2[0].hash

    def test_oversized_chunk_splits_on_paragraph(self):
        # Create a section with many paragraphs that exceeds max_chars
        big_section = "## Big\n" + ("A paragraph with some text.\n\n" * 200)
        chunks = chunk_markdown(big_section, "big.md", max_chars=300)
        # Should split into multiple chunks
        assert len(chunks) > 1
        # Each chunk should be under max_chars (approximately)
        for c in chunks:
            assert len(c.text) < 500  # Allow some slack

    def test_no_h2_headers(self):
        text = "Just some text without any headers."
        chunks = chunk_markdown(text, "plain.md")
        assert len(chunks) == 1
        assert chunks[0].start_line == 1

    def test_empty_text(self):
        chunks = chunk_markdown("", "empty.md")
        assert len(chunks) == 1  # One section for empty
        # Empty text produces minimal chunk

    def test_h3_not_split(self):
        """H3 headers should NOT split (only H2 splits)."""
        text = "## Main\nContent.\n### Sub\nMore content."
        chunks = chunk_markdown(text, "test.md")
        assert len(chunks) == 1  # Only one H2 section


class TestSplitOnH2:
    def test_basic(self):
        lines = ["## A", "content", "## B", "more"]
        sections = _split_on_h2(lines)
        assert len(sections) == 2
        assert sections[0] == (0, 2)  # lines 0-1
        assert sections[1] == (2, 4)  # lines 2-3

    def test_no_h2(self):
        lines = ["text", "more text"]
        sections = _split_on_h2(lines)
        assert len(sections) == 1
        assert sections[0] == (0, 2)

    def test_empty(self):
        assert _split_on_h2([]) == []
