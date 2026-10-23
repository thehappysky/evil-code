"""Markdown chunking by H2 headers.

References:
    - Rust chunker.rs

Strategy:
    1. Primary split: on ## (H2) header boundaries
    2. Secondary split: oversized chunks (> max_chars) split on paragraph
       boundaries (double newline)
    3. Each chunk records its line range in the source file
    4. Chunk IDs are deterministically generated from path + position
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class Chunk:
    """A single text chunk from a Markdown document.

    Attributes:
        id: Unique identifier, e.g. "MEMORY.md:0"
        text: Chunk content text.
        start_line: 1-indexed start line in source file.
        end_line: 1-indexed end line in source file.
        hash: blake3 (or hashlib) hex digest for change detection.
    """

    id: str
    text: str
    start_line: int
    end_line: int
    hash: str


def chunk_markdown(
    text: str,
    source_path: str,
    max_chars: int = 16000,
) -> list[Chunk]:
    """Split Markdown text into indexed chunks.

    Args:
        text: Raw Markdown text content.
        source_path: Path identifier for chunk ID generation.
        max_chars: Maximum characters per chunk before secondary split.

    Returns:
        List of Chunk objects, ordered by position in source.
    """
    lines = text.split("\n")
    total_lines = len(lines)

    # Step 1: Split on H2 boundaries
    sections = _split_on_h2(lines)

    # Step 2: Split oversized sections on paragraph boundaries
    chunks: list[Chunk] = []
    for section_start, section_end in sections:
        section_text = "\n".join(lines[section_start:section_end])
        if len(section_text) <= max_chars:
            chunks.append(
                _make_chunk(
                    source_path, len(chunks), section_text,
                    section_start + 1, section_end,
                )
            )
        else:
            # Secondary split on paragraph boundaries
            sub_chunks = _split_oversized(
                source_path, len(chunks), lines,
                section_start, section_end, max_chars,
            )
            chunks.extend(sub_chunks)

    return chunks


def _split_on_h2(lines: list[str]) -> list[tuple[int, int]]:
    """Split lines into sections on ## headers.

    Returns:
        List of (start_line, end_line) 0-indexed tuples.
    """
    if not lines:
        return []

    # Find all H2 positions
    h2_positions: list[int] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            h2_positions.append(i)

    if not h2_positions:
        # No H2 headers, treat entire text as one section
        return [(0, len(lines))]

    sections: list[tuple[int, int]] = []
    for j, pos in enumerate(h2_positions):
        start = pos
        end = h2_positions[j + 1] if j + 1 < len(h2_positions) else len(lines)
        sections.append((start, end))

    return sections


def _split_oversized(
    source_path: str,
    base_index: int,
    lines: list[str],
    section_start: int,
    section_end: int,
    max_chars: int,
) -> list[Chunk]:
    """Split an oversized section on paragraph (double newline) boundaries."""
    chunks: list[Chunk] = []
    current_start = section_start
    current_chars = 0
    chunk_idx = base_index

    for i in range(section_start, section_end):
        line = lines[i]
        line_chars = len(line) + 1  # +1 for newline

        if (
            current_chars + line_chars > max_chars
            and current_chars > 0
            and line.strip() == ""
        ):
            # Flush current chunk at paragraph boundary
            section_text = "\n".join(lines[current_start:i])
            chunks.append(
                _make_chunk(
                    source_path, chunk_idx, section_text,
                    current_start + 1, i,
                )
            )
            chunk_idx += 1
            current_start = i + 1  # Skip the blank line
            current_chars = 0
        else:
            current_chars += line_chars

    # Flush remaining
    if current_start < section_end:
        section_text = "\n".join(lines[current_start:section_end])
        chunks.append(
            _make_chunk(
                source_path, chunk_idx, section_text,
                current_start + 1, section_end,
            )
        )

    return chunks


def _make_chunk(
    source_path: str,
    index: int,
    text: str,
    start_line: int,
    end_line: int,
) -> Chunk:
    """Create a Chunk with computed id and hash."""
    chunk_id = f"{source_path}:{index}"
    # Use blake3 if available, otherwise fall back to hashlib sha256
    try:
        import blake3
        chunk_hash = blake3.blake3(text.encode("utf-8")).hexdigest()
    except ImportError:
        chunk_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    return Chunk(
        id=chunk_id,
        text=text.strip(),
        start_line=start_line,
        end_line=end_line,
        hash=chunk_hash,
    )
