"""Safety invariants and denylist enforcement (docs/09_SAFETY.md)."""

from __future__ import annotations

import os
from pathlib import Path

from .models import SafetyRefusal

DENYLIST: frozenset[Path] = frozenset(
    [
        Path("/"),
        Path("/bin"),
        Path("/boot"),
        Path("/dev"),
        Path("/etc"),
        Path("/lib"),
        Path("/lib32"),
        Path("/lib64"),
        Path("/proc"),
        Path("/run"),
        Path("/sbin"),
        Path("/sys"),
        Path("/usr"),
        Path("/var/lib"),
        Path("/var/log"),
    ]
)


def refuse_root(*, allow_root: bool = False) -> None:
    """Refuse execution if running as root (UID 0) unless allow_root is True.

    Raises SafetyRefusal (mapped to exit code 4 by the CLI).
    """
    if not allow_root and hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SafetyRefusal(
            "Refused: running as root (UID 0) is forbidden for safety. "
            "Pass --allow-root to override."
        )


_SLASH_PATH = Path("/")
_ROOT_PATH = Path("/root")
_NON_ROOT_DENY: frozenset[Path] = frozenset(p for p in DENYLIST if p != _SLASH_PATH)
_NON_ROOT_DENY_WITH_ROOT: frozenset[Path] = _NON_ROOT_DENY | {_ROOT_PATH}


def is_denylisted(path: Path, *, allow_root: bool = False) -> bool:
    """Check whether a path resolves into the system denylist."""
    real = Path(os.path.realpath(str(path)))

    if real == _SLASH_PATH:
        return True

    all_ancestors = set(real.parents)
    all_ancestors.add(real)
    target_set = _NON_ROOT_DENY if allow_root else _NON_ROOT_DENY_WITH_ROOT
    return not target_set.isdisjoint(all_ancestors)


def check_source_root(path: Path, *, recursive: bool = False, allow_root: bool = False) -> None:
    """Refuse source roots that are in the system denylist or recursive $HOME.

    Raises SafetyRefusal if path is forbidden.
    """
    refuse_root(allow_root=allow_root)

    real = Path(os.path.realpath(str(path)))

    if is_denylisted(real, allow_root=allow_root):
        raise SafetyRefusal(f"Source root '{path}' resolves to denylisted system path '{real}'.")

    try:
        home = Path(os.path.realpath(str(Path.home())))
        if recursive and real == home:
            raise SafetyRefusal(
                f"Recursive watch or scan of $HOME ('{path}') is refused for safety. "
                "Organizing $HOME recursively would affect system and application data."
            )
    except RuntimeError:
        pass


def check_destination(path: Path, *, allow_root: bool = False) -> None:
    """Refuse destinations that are in the system denylist or contain hidden components.

    Raises SafetyRefusal if path is forbidden.
    """
    refuse_root(allow_root=allow_root)

    # Check components of the path itself
    for part in path.parts:
        if part in ("/", "\\"):
            continue
        if part.startswith("."):
            raise SafetyRefusal(f"Destination '{path}' contains hidden component '{part}'.")

    real = Path(os.path.realpath(str(path)))

    if is_denylisted(real, allow_root=allow_root):
        raise SafetyRefusal(f"Destination '{path}' resolves to denylisted system path '{real}'.")

    # Check components after resolving symlinks
    for part in real.parts:
        if part in ("/", "\\"):
            continue
        if part.startswith("."):
            raise SafetyRefusal(
                f"Destination '{path}' resolves to hidden component '{part}' in '{real}'."
            )
