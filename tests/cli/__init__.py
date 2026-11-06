"""CLI test package initialization ensuring local worktree src is loaded first."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

for mod in list(sys.modules.keys()):
    if mod.startswith("clutterxp"):
        del sys.modules[mod]
