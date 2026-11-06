"""Serializers for duplicate groups (JSON and CSV formats)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from clutterxp.models import DuplicateGroup


def groups_to_json(
    groups: Sequence[DuplicateGroup],
    folders: Sequence[Path],
    file_count: int,
    total_bytes: int,
) -> dict[str, Any]:
    """Returns the JSON shape from docs/07 section 4."""
    exact_list: list[dict[str, Any]] = []
    hardlinks_list: list[dict[str, Any]] = []
    potential_list: list[dict[str, Any]] = []

    exact_groups_count = 0
    total_wasted_bytes = 0
    potential_groups_count = 0

    for g in groups:
        if g.kind == "exact":
            exact_groups_count += 1
            total_wasted_bytes += g.wasted_bytes
            exact_list.append(
                {
                    "sha256": g.sha256 or "",
                    "size": g.files[0].size if g.files else 0,
                    "wasted_bytes": g.wasted_bytes,
                    "files": [
                        {
                            "path": str(f.path),
                            "mtime_ns": f.mtime_ns,
                            "inode": f.inode,
                        }
                        for f in g.files
                    ],
                }
            )
        elif g.kind == "hardlink":
            hardlinks_list.append(
                {
                    "files": [str(f.path) for f in g.files],
                }
            )
        elif g.kind == "potential":
            potential_groups_count += 1
            potential_list.append(
                {
                    "note": g.note,
                    "files": [str(f.path) for f in g.files],
                }
            )

    return {
        "schema_version": 1,
        "scanned": {
            "folders": [str(p) for p in folders],
            "files": file_count,
            "bytes": total_bytes,
        },
        "exact": exact_list,
        "hardlinks": hardlinks_list,
        "potential": potential_list,
        "summary": {
            "exact_groups": exact_groups_count,
            "wasted_bytes": total_wasted_bytes,
            "potential_groups": potential_groups_count,
        },
    }


def groups_to_csv_rows(groups: Sequence[DuplicateGroup]) -> list[list[str]]:
    """Returns [header_row, data_rows...] for CSV output."""
    header = [
        "group_id",
        "kind",
        "sha256",
        "size",
        "wasted_bytes",
        "note",
        "path",
        "mtime_ns",
        "inode",
    ]
    rows: list[list[str]] = [header]

    for idx, g in enumerate(groups, start=1):
        for f in g.files:
            rows.append(
                [
                    str(idx),
                    g.kind,
                    g.sha256 or "",
                    str(f.size),
                    str(g.wasted_bytes),
                    g.note,
                    str(f.path),
                    str(f.mtime_ns),
                    str(f.inode),
                ]
            )

    return rows
