"""Path encoding and decoding for SQLite storage of arbitrary POSIX paths.

Linux file names are bytes. Python gives str with surrogate escapes for non-UTF-8
names, which SQLite cannot store. This codec provides transparent round-tripping
by base64-encoding non-UTF-8 byte sequences with a sentinel prefix.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

MARKER = "\x01b64:"


def encode(path: Path) -> str:
    """Encode path to str safe for SQLite storage.

    If fsencode(path) is valid UTF-8, return str(path).
    Otherwise return MARKER + base64(raw bytes).
    """
    raw_bytes = os.fsencode(path)
    try:
        raw_bytes.decode("utf-8")
        return os.fspath(path)
    except UnicodeDecodeError:
        encoded_b64 = base64.b64encode(raw_bytes).decode("ascii")
        return f"{MARKER}{encoded_b64}"


def decode(text: str) -> Path:
    """Reverse of encode.

    If text starts with MARKER, base64-decode and fsdecode.
    Otherwise Path(text).
    """
    if text.startswith(MARKER):
        raw_b64 = text[len(MARKER) :]
        raw_bytes = base64.b64decode(raw_b64.encode("ascii"))
        return Path(os.fsdecode(raw_bytes))
    return Path(text)
