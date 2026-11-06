"""Tests for pathcodec: UTF-8, non-UTF-8, hypothesis property tests, and A39."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from clutterxp.history.pathcodec import MARKER, decode, encode


def test_utf8_path_round_trip_unchanged() -> None:
    """Normal UTF-8 paths should be stored as plain strings without marker."""
    paths = [
        Path("/home/user/Downloads/report.pdf"),
        Path("relative/nested/dir/file.txt"),
        Path("/tmp/résumé_2026.docx"),
        Path("/data/日本語/ドキュメント.txt"),
        Path("/var/log/app.2026-11-06.log"),
    ]
    for p in paths:
        encoded = encode(p)
        assert not encoded.startswith(MARKER)
        assert encoded == os.fspath(p)
        assert decode(encoded) == p


def test_non_utf8_bytes_round_trip() -> None:
    """Paths containing non-UTF-8 bytes must be encoded with MARKER + base64 and round-trip."""
    raw_bytes = b"/tmp/invalid_\xff\xfe\x80_bytes.bin"
    path = Path(os.fsdecode(raw_bytes))

    encoded = encode(path)
    assert encoded.startswith(MARKER)

    decoded = decode(encoded)
    assert decoded == path
    assert os.fsencode(decoded) == raw_bytes


@given(st.binary(min_size=1).filter(lambda b: b"\x00" not in b))
def test_hypothesis_random_bytes_round_trip(b: bytes) -> None:
    """Hypothesis: any POSIX-valid raw byte sequence round-trips identically."""
    p = Path(os.fsdecode(b))
    encoded = encode(p)
    decoded = decode(encoded)
    assert decoded == p
    assert os.fsencode(decoded) == os.fsencode(p)


def test_A39_non_utf8_names_round_trip() -> None:
    """A39: Non-UTF-8 file names round-trip through history storage."""
    # Test multiple non-UTF-8 byte sequences including high-byte sequences
    test_cases = [
        b"/home/user/Downloads/file_\x80\x81\x82.dat",
        b"/home/user/\xff\xfe\xfd/test.pdf",
        b"/tmp/\xc0\xc1_invalid_utf8.bin",
    ]

    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE paths (id INTEGER PRIMARY KEY, p TEXT NOT NULL);")

    for raw in test_cases:
        original_path = Path(os.fsdecode(raw))
        encoded_str = encode(original_path)

        # SQLite can store the encoded string without error
        conn.execute("INSERT INTO paths (p) VALUES (?);", (encoded_str,))
        row = conn.execute("SELECT p FROM paths ORDER BY id DESC LIMIT 1;").fetchone()
        stored_text = row[0]

        restored_path = decode(stored_text)
        assert restored_path == original_path
        assert os.fsencode(restored_path) == raw
