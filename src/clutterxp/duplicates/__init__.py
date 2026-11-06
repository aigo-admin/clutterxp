"""Duplicate detection: multi-stage pipeline, hash cache, and name normalization."""

from __future__ import annotations

from .detector import (
    DuplicateDetectorImpl,
    compute_full_hash,
    compute_partial_hash,
)
from .hashcache import HashCache
from .namenorm import normalize_stem
from .report import groups_to_csv_rows, groups_to_json

__all__ = [
    "DuplicateDetectorImpl",
    "HashCache",
    "compute_full_hash",
    "compute_partial_hash",
    "groups_to_csv_rows",
    "groups_to_json",
    "normalize_stem",
]
