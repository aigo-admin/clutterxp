"""Safe mover: atomic operations, collision handling, and verification."""

from __future__ import annotations

from clutterxp.mover.collision import candidate_names, split_name_ext
from clutterxp.mover.fsops import (
    checked_rename,
    fsync_dir,
    fsync_file,
    link_unlink,
    rename_noreplace,
    same_device,
)
from clutterxp.mover.safe_move import SafeMover

__all__ = [
    "SafeMover",
    "candidate_names",
    "checked_rename",
    "fsync_dir",
    "fsync_file",
    "link_unlink",
    "rename_noreplace",
    "same_device",
    "split_name_ext",
]
