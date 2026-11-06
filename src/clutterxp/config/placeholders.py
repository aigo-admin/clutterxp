"""Allowed destination template placeholders and validator (docs/02_RULE_ENGINE.md)."""

from __future__ import annotations

import difflib
import re

ALLOWED_PLACEHOLDERS: frozenset[str] = frozenset(
    [
        "year",
        "month",
        "day",
        "ext",
        "rule",
        "mtime",
    ]
)


def extract_static_root(template: str) -> str:
    """Extract the path portion before the first component containing a placeholder."""
    parts = template.split("/")
    static_parts: list[str] = []
    for part in parts:
        if "{" in part:
            break
        static_parts.append(part)

    if not static_parts or (len(static_parts) == 1 and static_parts[0] == ""):
        return "/" if template.startswith("/") else "."

    res = "/".join(static_parts)
    return res if res else ("/" if template.startswith("/") else ".")


def validate_destination_template(template: str) -> list[str]:
    """Validate placeholder names and formats in a destination template string.

    Returns a list of error descriptions (empty if valid).
    """
    errors: list[str] = []

    # Check for unmatched brackets
    open_count = template.count("{")
    close_count = template.count("}")
    if open_count != close_count:
        errors.append(f"Unmatched brackets in destination template '{template}'")
        return errors

    # Check for empty placeholder {}
    if "{}" in template:
        errors.append("Empty placeholder '{}' in destination template")

    # Find all placeholder contents
    placeholders = re.findall(r"\{([^}]*)\}", template)
    for p in placeholders:
        if not p:
            continue
        if p.startswith("mtime:"):
            fmt = p[6:]
            if not fmt:
                errors.append("Empty mtime format in destination template")
            elif "/" in fmt or "\x00" in fmt:
                errors.append(
                    f"mtime format '{fmt}' in destination template cannot contain '/' or NUL"
                )
        elif p in ALLOWED_PLACEHOLDERS:
            continue
        else:
            close = difflib.get_close_matches(p, sorted(ALLOWED_PLACEHOLDERS))
            if close:
                errors.append(
                    f"Unknown placeholder '{{{p}}}' in destination template; "
                    f"did you mean '{{{close[0]}}}'?"
                )
            else:
                errors.append(f"Unknown placeholder '{{{p}}}' in destination template")

    return errors
