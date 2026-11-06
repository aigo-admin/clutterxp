"""Organizer tests package."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure worktree's src directory is prioritized in sys.path
_src_path = str(Path(__file__).resolve().parent.parent.parent / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)
