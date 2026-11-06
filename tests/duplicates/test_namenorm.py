"""Tests for filename normalization and copy-pattern stripping."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from clutterxp.duplicates.namenorm import normalize_stem

GOLDEN_TABLE: list[tuple[str, str]] = [
    ("report (1)", "report"),
    ("report(2)", "report"),
    ("report - copy", "report"),
    ("report copy", "report"),
    ("report_copy", "report"),
    ("report-copy", "report"),
    ("report_1", "report"),
    ("report-1", "report"),
    ("report2", "report2"),  # NOT normalized (digit without separator)
    ("my document (1)", "my document"),
    ("final_final", "final_final"),  # NOT stripped (not a copy pattern)
]


@pytest.mark.parametrize(("raw", "expected"), GOLDEN_TABLE)
def test_A57_potential_duplicates_closed_name_patterns(raw: str, expected: str) -> None:
    """A57: Potential duplicate name normalization strips only closed-list patterns."""
    assert normalize_stem(raw) == expected


def test_chained_copy_patterns_stripped() -> None:
    """Chained copy markers are stripped idempotently."""
    assert normalize_stem("report (1) (2)") == "report"
    assert normalize_stem("report - copy_1") == "report"
    assert normalize_stem("photo_copy-1 (2)") == "photo"


def test_casefolding() -> None:
    """Normalization casefolds the stem."""
    assert normalize_stem("Report - Copy") == "report"
    assert normalize_stem("DOCUMENT (1)") == "document"
    assert normalize_stem("Invoice_1") == "invoice"


def test_empty_or_pattern_only_stem() -> None:
    """Stem with only copy pattern does not strip to empty string."""
    assert normalize_stem("(1)") == "(1)"
    assert normalize_stem("") == ""


@given(st.text())
def test_normalize_stem_idempotent(s: str) -> None:
    """normalize_stem is idempotent for all text inputs."""
    once = normalize_stem(s)
    twice = normalize_stem(once)
    assert once == twice
