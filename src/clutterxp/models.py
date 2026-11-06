"""ClutterXP shared contracts (FROZEN).

Every module imports its data types, enums and exceptions from this file.
Changing anything here requires a CONTRACT_CHANGE request (see AGENTS.md).

Design rules:
  * Plain frozen dataclasses, no business logic.
  * Paths are pathlib.Path; never str.
  * Times are integer nanoseconds (st_mtime_ns) for exact comparison.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1  # JSON output schema version and config schema version

# (bytes_done, bytes_total) - called during slow operations (copy, hashing)
ProgressCb = Callable[[int, int], None]


# --------------------------------------------------------------------------- enums
class MatchMode(str, Enum):
    TOKEN = "token"  # default: keyword must equal whole filename token(s)
    PREFIX = "prefix"  # a filename token starts with the keyword
    SUBSTRING = "substring"  # keyword anywhere in the casefolded stem
    GLOB = "glob"  # fnmatch against the full filename
    REGEX = "regex"  # re.search against the full filename


class CollisionPolicy(str, Enum):
    SKIP = "skip"  # leave the file where it is, report BLOCKED/COLLISION
    RENAME = "rename"  # add " (1)", " (2)", ... before the extension


class CreateDestinations(str, Enum):
    NEVER = "never"  # destination must already exist
    LEAF = (
        "leaf"  # create last component of the static root (parent must exist) + templated subdirs
    )
    ALL = "all"  # mkdir -p


class DecisionKind(str, Enum):
    MATCH = "match"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    IGNORED = "ignored"  # hidden / temp / ignore-pattern / not a regular file


class PlanStatus(str, Enum):
    WILL_MOVE = "will_move"
    WILL_MOVE_RENAMED = "will_move_renamed"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    IGNORED = "ignored"
    BLOCKED = "blocked"  # see ErrorCode


class OpStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    UNDONE = "undone"
    FAILED = "failed"
    SKIPPED = "skipped"


class MoveStrategy(str, Enum):
    RENAME_NOREPLACE = "renameat2"  # renameat2(RENAME_NOREPLACE), one atomic syscall
    LINK_UNLINK = "link_unlink"  # os.link (fails if exists) then os.unlink(src)
    CHECKED_RENAME = "checked_rename"  # lock-protected exists-check + os.rename (last resort)
    COPY_VERIFY = "copy_verify"  # cross-filesystem: temp copy, fsync, verify, publish, unlink src


class ErrorCode(str, Enum):
    NONE = "none"
    COLLISION = "collision"
    DEST_MISSING = "dest_missing"
    DEST_NOT_WRITABLE = "dest_not_writable"
    DEST_INVALID = "dest_invalid"  # inside watched folder, system dir, traversal, etc.
    SRC_VANISHED = "src_vanished"
    SRC_CHANGED = "src_changed"  # size/mtime changed during the operation
    SRC_NOT_REGULAR = "src_not_regular"  # directory, symlink, device, socket...
    PERMISSION = "permission"
    NO_SPACE = "no_space"
    VERIFY_FAILED = "verify_failed"
    LOCK_TIMEOUT = "lock_timeout"
    UNDO_NOT_DONE = "undo_not_done"
    UNDO_DEST_CHANGED = "undo_dest_changed"
    UNDO_ORIGIN_OCCUPIED = "undo_origin_occupied"
    UNDO_DEST_MISSING = "undo_dest_missing"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- errors
class ClutterError(Exception):
    """Base class for all expected, user-reportable errors."""

    def __init__(self, message: str, code: ErrorCode = ErrorCode.UNKNOWN) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ConfigError(ClutterError):
    """Invalid configuration. `problems` holds one human-readable line per issue."""

    def __init__(self, message: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(message, ErrorCode.UNKNOWN)
        self.problems = problems


class SafetyRefusal(ClutterError):
    """Operation refused by a safety invariant (root user, system directory, ...)."""


# --------------------------------------------------------------------------- file + rules
@dataclass(frozen=True, slots=True)
class FileInfo:
    path: Path
    name: str
    stem: str
    ext: str  # last suffix, lowercase, no dot; "" if none
    size: int
    mtime_ns: int
    inode: int
    device: int
    is_regular: bool = True
    is_symlink: bool = False

    @classmethod
    def from_path(cls, path: Path | str) -> "FileInfo":
        p = Path(path)
        st = p.lstat()
        suffix = p.suffix
        return cls(
            path=p,
            name=p.name,
            stem=p.stem,
            ext=suffix[1:].lower() if suffix else "",
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            inode=st.st_ino,
            device=st.st_dev,
            is_regular=stat.S_ISREG(st.st_mode),
            is_symlink=stat.S_ISLNK(st.st_mode),
        )


@dataclass(frozen=True, slots=True)
class RuleHit:
    rule_name: str
    priority: int
    dest_dir: Path  # fully expanded (~, $VARS, templates) destination directory
    static_root: Path  # dest path up to the first template placeholder
    matched_by: str  # human text, e.g. "keyword 'sl' (token)"
    collision: CollisionPolicy
    create: CreateDestinations
    also_matched: tuple[str, ...] = ()  # lower-priority / same-destination rules that also matched


@dataclass(frozen=True, slots=True)
class Decision:
    file: FileInfo
    kind: DecisionKind
    hit: RuleHit | None = None
    candidates: tuple[RuleHit, ...] = ()  # populated when kind == AMBIGUOUS
    reason: str = ""  # populated for NO_MATCH / IGNORED


@dataclass(frozen=True, slots=True)
class RuleTrace:
    """One line of `rules test` output: why a rule did or did not match."""

    rule_name: str
    matched: bool
    priority: int
    reasons: tuple[str, ...]


# --------------------------------------------------------------------------- planning + moving
@dataclass(frozen=True, slots=True)
class PlannedAction:
    file: FileInfo
    status: PlanStatus
    decision: Decision
    dest_path: Path | None = None
    code: ErrorCode = ErrorCode.NONE
    message: str = ""


@dataclass(frozen=True, slots=True)
class Plan:
    folders: tuple[Path, ...]
    actions: tuple[PlannedAction, ...]

    def count(self, status: PlanStatus) -> int:
        return sum(1 for a in self.actions if a.status is status)


@dataclass(frozen=True, slots=True)
class MoveResult:
    ok: bool
    src: Path
    dst: Path | None
    strategy: MoveStrategy | None
    code: ErrorCode = ErrorCode.NONE
    message: str = ""
    created_dirs: tuple[Path, ...] = ()
    renamed_due_to_collision: bool = False
    bytes_copied: int = 0


# --------------------------------------------------------------------------- history + undo
@dataclass(frozen=True, slots=True)
class OperationRecord:
    id: int
    batch_id: int
    status: OpStatus
    src_path: Path
    dst_path: Path | None
    rule_name: str | None
    strategy: MoveStrategy | None
    size: int | None
    mtime_ns: int | None
    inode: int | None
    device: int | None
    sha256: str | None
    created_dirs: tuple[Path, ...]
    created_at: str  # ISO-8601 UTC
    finished_at: str | None
    undone_at: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class UndoOutcome:
    op_id: int
    ok: bool
    restored_to: Path | None = None
    code: ErrorCode = ErrorCode.NONE
    message: str = ""


@dataclass(frozen=True, slots=True)
class BatchResult:
    batch_id: int | None
    moved: int
    skipped: int
    blocked: int
    failed: int
    results: tuple[MoveResult, ...] = ()


# --------------------------------------------------------------------------- duplicates
@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    kind: str  # "exact" | "potential" | "hardlink"
    files: tuple[FileInfo, ...]
    sha256: str | None = None  # set for kind == "exact"
    wasted_bytes: int = 0  # (n-1) * size for exact groups, 0 otherwise
    note: str = ""  # e.g. why files are "potential" candidates
