"""Destination template expansion and static root calculation.

Implements ~ and $VAR expansion, placeholder substitution ({year}, {month},
{day}, {ext}, {rule}, {mtime:...}), value sanitization, and static root
containment per docs/02_RULE_ENGINE.md section 7 and safety invariant S13.
"""

from __future__ import annotations

import datetime
import difflib
import os
import re
from pathlib import Path

from clutterxp.models import ClutterError, ErrorCode, FileInfo

ALLOWED_PLACEHOLDERS = frozenset(["year", "month", "day", "ext", "rule", "mtime"])

_ENV_VAR_RE = re.compile(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|(?:\{([A-Za-z_][A-Za-z0-9_]*)\}))")
_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")


def static_root(template: str) -> str:
    """Returns the path prefix before the first {placeholder}."""
    first_brace = template.find("{")
    if first_brace == -1:
        return template

    prefix = template[:first_brace]
    last_slash = prefix.rfind("/")
    if last_slash == -1:
        return ""
    if last_slash == 0:
        return "/"
    return prefix[:last_slash]


def _expand_env_and_tilde(text: str, environ: dict[str, str] | None) -> str:
    """Expand ~ and $VAR / ${VAR} in text.

    Raises ClutterError(code=DEST_INVALID) if any $VAR is unset.
    """
    env = os.environ if environ is None else environ

    def replace_var(m: re.Match[str]) -> str:
        var_name = m.group(1) or m.group(2)
        if var_name not in env:
            raise ClutterError(
                f"unresolved environment variable: ${var_name}",
                code=ErrorCode.DEST_INVALID,
            )
        return env[var_name]

    expanded = _ENV_VAR_RE.sub(replace_var, text)

    # Expand tilde ~
    if expanded == "~" or expanded.startswith("~/"):
        home = env.get("HOME")
        if not home:
            home = str(Path.home())
        expanded = home + expanded[1:]

    return expanded


def expand(
    template: str,
    file: FileInfo,
    rule_name: str,
    *,
    now_ns: int | None = None,  # nanoseconds, default to file mtime
    environ: dict[str, str] | None = None,
) -> Path:
    """Expand template to absolute Path.

    Raises ClutterError(code=DEST_INVALID) if:
    - Substituted value contains '/' or NUL
    - Substituted value is '.' or '..'
    - Unknown placeholder (did-you-mean in message)
    - {mtime:} with '/' in format string
    - Expanded destination escapes static root (S13)
    First expands ~ and $VAR, then {placeholders}.
    Result is os.path.normpath'd (NOT realpath, no symlink resolution).
    """
    # 1. Expand ~ and environment variables
    expanded_template = _expand_env_and_tilde(template, environ)

    # Calculate static root of the template
    raw_static = static_root(template)
    expanded_static = _expand_env_and_tilde(raw_static, environ)
    norm_static = os.path.normpath(expanded_static)
    static_root_path = Path(norm_static)

    # 2. Prepare timestamp for time-based placeholders
    timestamp_ns = now_ns if now_ns is not None else file.mtime_ns
    dt = datetime.datetime.fromtimestamp(timestamp_ns / 1_000_000_000)

    # 3. Substitute {placeholders}
    def replace_placeholder(m: re.Match[str]) -> str:
        content = m.group(1)
        val: str

        if content.startswith("mtime:"):
            fmt = content[6:]
            if "/" in fmt or "\x00" in fmt:
                raise ClutterError(
                    f"strftime format in {{mtime:...}} cannot contain '/' or NUL: {fmt!r}",
                    code=ErrorCode.DEST_INVALID,
                )
            try:
                val = dt.strftime(fmt)
            except ValueError as e:
                raise ClutterError(
                    f"invalid strftime format {fmt!r}: {e}",
                    code=ErrorCode.DEST_INVALID,
                ) from e
        elif content == "mtime":
            val = dt.strftime("%Y%m%d_%H%M%S")
        elif content == "year":
            val = f"{dt.year:04d}"
        elif content == "month":
            val = f"{dt.month:02d}"
        elif content == "day":
            val = f"{dt.day:02d}"
        elif content == "ext":
            val = file.ext.lower() if file.ext else "noext"
        elif content == "rule":
            val = rule_name.replace("/", "").replace("\\", "").replace("\x00", "")
        else:
            close = difflib.get_close_matches(content, list(ALLOWED_PLACEHOLDERS), n=1)
            hint = f" (did you mean '{close[0]}' ?)" if close else ""
            raise ClutterError(
                f"unknown placeholder '{content}'{hint}",
                code=ErrorCode.DEST_INVALID,
            )

        if "/" in val or "\x00" in val:
            raise ClutterError(
                f"substituted value for '{{{content}}}' contains '/' or NUL: {val!r}",
                code=ErrorCode.DEST_INVALID,
            )
        if val in (".", ".."):
            raise ClutterError(
                f"substituted value for '{{{content}}}' cannot be '.' or '..': {val!r}",
                code=ErrorCode.DEST_INVALID,
            )

        return val

    expanded_path_str = _PLACEHOLDER_RE.sub(replace_placeholder, expanded_template)

    norm_dest = os.path.normpath(expanded_path_str)
    dest_path = Path(norm_dest)

    if not dest_path.is_absolute():
        raise ClutterError(
            f"expanded destination must be absolute: {dest_path}",
            code=ErrorCode.DEST_INVALID,
        )

    # 4. Containment check (S13): cannot escape static root
    if static_root_path.is_absolute() and (
        dest_path != static_root_path and static_root_path not in dest_path.parents
    ):
        raise ClutterError(
            f"expanded destination {dest_path} escapes static root {static_root_path}",
            code=ErrorCode.DEST_INVALID,
        )

    return dest_path
