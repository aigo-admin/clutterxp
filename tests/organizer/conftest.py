"""Pytest configuration and fixtures for tests/organizer."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure this worktree's src is first on sys.path and in clutterxp.__path__
_worktree_src = Path(__file__).resolve().parent.parent.parent / "src"
_src_str = str(_worktree_src)
if _src_str not in sys.path:
    sys.path.insert(0, _src_str)

import clutterxp  # noqa: E402

_clutterxp_dir = str(_worktree_src / "clutterxp")
if _clutterxp_dir not in clutterxp.__path__:
    clutterxp.__path__.insert(0, _clutterxp_dir)
