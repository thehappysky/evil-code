"""Atomic file write utility.

Shared by file_edit and file_write tools to avoid code duplication.
Pattern: temp file in same directory -> write -> flush -> fsync -> os.replace.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically write content to path.

    Uses temp file in the same directory + os.replace() to ensure
    no half-written files on interrupt. The temp file is created in
    the target directory to guarantee same-filesystem rename.

    Args:
        path: Target file path.
        content: Text content to write.
        encoding: File encoding (default utf-8).

    Raises:
        OSError: If write or rename fails.
    """
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
