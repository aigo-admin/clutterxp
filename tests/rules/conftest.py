"""Pytest configuration for rules tests.

Ensures the worktree's src directory is prioritized in sys.path and clutterxp.__path__.
"""

from __future__ import annotations

import sys
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[2] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

try:
    import clutterxp

    clutterxp_dir = str(Path(src_dir) / "clutterxp")
    if clutterxp_dir not in clutterxp.__path__:
        clutterxp.__path__.insert(0, clutterxp_dir)
except ImportError:
    pass
