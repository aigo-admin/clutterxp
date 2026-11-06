"""Collision candidate generator for rename policies."""

from __future__ import annotations

from collections.abc import Generator

COMPOUND_EXTS: tuple[str, ...] = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
)


def split_name_ext(name: str) -> tuple[str, str]:
    """Split filename into stem and extension, preserving compound extensions.

    Compound extensions like .tar.gz are treated as a single extension.
    For names with multiple dots like report.2024.pdf, only the last extension is split.
    Leading dots (e.g. .gitignore) are treated as part of the stem unless there is
    another dot (e.g. .bashrc.bak).
    """
    lower_name = name.lower()
    for compound in COMPOUND_EXTS:
        if lower_name.endswith(compound) and len(name) > len(compound):
            return name[: -len(compound)], name[-len(compound) :]

    dot_index = name.rfind(".")
    if dot_index > 0:  # Dot is not the first character
        return name[:dot_index], name[dot_index:]
    return name, ""


def candidate_names(name: str) -> Generator[str, None, None]:
    """Yield name (1).ext, name (2).ext, ... up to 9999.

    Handles compound extensions (.tar.gz): stem is everything before .tar.gz.
    Handles no-extension names.
    name (1).ext format: space before parens.
    """
    stem, ext = split_name_ext(name)
    for i in range(1, 10000):
        yield f"{stem} ({i}){ext}"


__all__ = ["COMPOUND_EXTS", "candidate_names", "split_name_ext"]
