"""Text truncation strategies.

Logic reference: Rust version truncate.rs.
Pure functions, zero project-internal dependencies.

All functions return (result_text, was_truncated) tuples so callers can
decide whether to append hints for the model.
"""


def truncate_with_preview(
    text: str,
    max_chars: int,
    preview_chars: int | None = None,
    hint: str = "",
) -> tuple[str, bool]:
    """Truncate text keeping the head portion + footer hint.

    Args:
        text: Original text.
        max_chars: Maximum allowed characters.
        preview_chars: How many characters to keep from the head.
            Defaults to max_chars - len(hint) - 100 (for footer).
        hint: Optional hint text appended in the footer.

    Returns:
        Tuple of (truncated_text, was_truncated).
    """
    if len(text) <= max_chars:
        return text, False

    if preview_chars is None:
        preview_chars = max_chars - 200  # reserve space for footer

    preview_chars = max(0, min(preview_chars, max_chars - 100))
    head = text[:preview_chars]

    total_lines = text.count("\n") + 1
    kept_lines = head.count("\n") + 1

    footer_parts = [
        f"\n\n[Output truncated - {total_lines} total lines, "
        f"showing first {kept_lines}. "
    ]
    if hint:
        footer_parts.append(f"{hint}. ")
    footer_parts.append(
        f"Characters: {len(text)} total, {preview_chars} shown.]"
    )
    footer = "".join(footer_parts)

    result = head + footer
    # Ensure we don't exceed max_chars even with footer
    if len(result) > max_chars:
        result = head[: max_chars - len(footer)] + footer

    return result, True


def truncate_middle(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate text keeping head and tail, replacing middle with ellipsis.

    Useful for showing both the beginning and end of a file/output
    while omitting the middle section.

    Args:
        text: Original text.
        max_chars: Maximum allowed characters.

    Returns:
        Tuple of (truncated_text, was_truncated).
    """
    if len(text) <= max_chars:
        return text, False

    # Keep 45% head, 45% tail, 10% for ellipsis marker
    head_size = int(max_chars * 0.45)
    tail_size = int(max_chars * 0.45)
    omitted = len(text) - head_size - tail_size

    head = text[:head_size]
    tail = text[-tail_size:]

    # Try to break at line boundaries for readability
    last_newline = head.rfind("\n")
    if last_newline > head_size * 0.7:
        head = head[: last_newline + 1]

    first_newline = tail.find("\n")
    if first_newline >= 0 and first_newline < tail_size * 0.3:
        tail = tail[first_newline + 1:]

    middle_marker = f"\n\n[... {omitted} characters omitted ...]\n\n"
    result = head + middle_marker + tail

    return result, True
