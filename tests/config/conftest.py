"""Pytest fixtures and environment setup for config tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure worktree's src is prioritized in tests
_worktree_src = Path(__file__).resolve().parents[2] / "src"
if str(_worktree_src) not in sys.path:
    sys.path.insert(0, str(_worktree_src))

import clutterxp  # noqa: E402

_cxp_pkg = str(_worktree_src / "clutterxp")
if _cxp_pkg not in clutterxp.__path__:
    clutterxp.__path__.insert(0, _cxp_pkg)

import clutterxp.config  # noqa: E402

_cxp_cfg_pkg = str(_worktree_src / "clutterxp" / "config")
if _cxp_cfg_pkg not in clutterxp.config.__path__:
    clutterxp.config.__path__.insert(0, _cxp_cfg_pkg)
