"""Mover test package setup."""

import sys
from pathlib import Path

# Ensure this worktree's src directory takes precedence over the shared venv editable install
src_dir = str(Path(__file__).resolve().parents[2] / "src")
if sys.path[0] != src_dir:
    if src_dir in sys.path:
        sys.path.remove(src_dir)
    sys.path.insert(0, src_dir)
