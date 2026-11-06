"""Size and duration parsing shared by config validation and the rule engine.

Sizes: powers of 1024 (KB == KiB == 1024). Durations: s, m, h, d, w.
"""

from __future__ import annotations

import re

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")
_SIZE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gib": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
    "tib": 1024**4,
}
_DUR_RE = re.compile(r"^\s*(\d+)\s*([smhdw]?)\s*$", re.IGNORECASE)
_DUR_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_size(value: int | str) -> int:
    """'10KB' -> 10240, '1.5MB' -> 1572864, 2048 -> 2048. Raises ValueError."""
    if isinstance(value, bool):
        raise ValueError(f"invalid size: {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"size must not be negative: {value}")
        return value
    m = _SIZE_RE.match(value)
    if not m:
        raise ValueError(f"invalid size: {value!r} (examples: 500, 10KB, 1.5MB, 2GB)")
    number, unit = m.group(1), m.group(2).lower()
    if unit not in _SIZE_UNITS:
        raise ValueError(f"unknown size unit {m.group(2)!r} in {value!r}")
    return int(float(number) * _SIZE_UNITS[unit])


def parse_duration_seconds(value: int | str) -> int:
    """'2d' -> 172800, '90' -> 90 (seconds), '3h' -> 10800. Raises ValueError."""
    if isinstance(value, bool):
        raise ValueError(f"invalid duration: {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"duration must not be negative: {value}")
        return value
    m = _DUR_RE.match(value)
    if not m:
        raise ValueError(f"invalid duration: {value!r} (examples: 30s, 15m, 2h, 7d, 4w)")
    return int(m.group(1)) * _DUR_UNITS[m.group(2).lower()]
