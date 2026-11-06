"""Keyword compilation and matching module for ClutterXP rule engine.

Implements token, prefix, substring, glob, and regex match modes,
as well as catastrophic backtracking detection per docs/02_RULE_ENGINE.md section 4.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from dataclasses import dataclass

from clutterxp.models import MatchMode
from clutterxp.rules.tokenizer import keyword_tokens


@dataclass(frozen=True)
class MatchContext:
    """Pre-computed per-file data passed to matchers."""

    name: str  # original name
    casefolded_name: str
    stem: str  # from split_stem
    ext: str
    raw_toks: list[str]  # from raw_tokens
    camel_toks: list[str]  # from camel_tokens
    case_sensitive: bool


def check_catastrophic_regex(pattern: str) -> bool:
    """Returns True if pattern has nested unbounded quantifiers like (a+)+."""
    i = 0
    n = len(pattern)
    in_char_class = False

    class Frame:
        def __init__(self) -> None:
            self.has_unbounded = False

    stack: list[Frame] = []

    def is_unbounded_quantifier_at(idx: int) -> tuple[bool, int]:
        """Returns (is_unbounded, next_idx)."""
        if idx >= n:
            return False, idx
        ch = pattern[idx]
        if ch in ("*", "+"):
            next_idx = idx + 1
            if next_idx < n and pattern[next_idx] in ("?", "+"):
                next_idx += 1
            return True, next_idx
        if ch == "{":
            end = pattern.find("}", idx)
            if end != -1:
                content = pattern[idx + 1 : end].strip()
                if "," in content:
                    parts = content.split(",", 1)
                    lower, upper = parts[0].strip(), parts[1].strip()
                    if (lower == "" or lower.isdigit()) and upper == "":
                        next_idx = end + 1
                        if next_idx < n and pattern[next_idx] in ("?", "+"):
                            next_idx += 1
                        return True, next_idx
        return False, idx

    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue

        if in_char_class:
            if ch == "]":
                in_char_class = False
            i += 1
            continue

        if ch == "[":
            in_char_class = True
            i += 1
            continue

        if ch == "(":
            stack.append(Frame())
            if i + 1 < n and pattern[i + 1] == "?":
                i += 2
                if i < n and pattern[i] in (":", "=", "!", ">"):
                    i += 1
                elif i < n and pattern[i] == "<":
                    end_name = pattern.find(">", i)
                    if end_name != -1:
                        i = end_name + 1
            else:
                i += 1
            continue

        if ch == ")":
            popped = stack.pop() if stack else None
            is_unbounded, next_i = is_unbounded_quantifier_at(i + 1)
            if is_unbounded:
                if popped is not None and popped.has_unbounded:
                    return True
                if stack:
                    stack[-1].has_unbounded = True
                i = next_i
                continue
            if popped is not None and popped.has_unbounded and stack:
                stack[-1].has_unbounded = True
            i += 1
            continue

        is_unbounded, next_i = is_unbounded_quantifier_at(i)
        if is_unbounded:
            if stack:
                stack[-1].has_unbounded = True
            i = next_i
            continue

        i += 1

    return False


def _is_contiguous_subsequence(sub: list[str], target: list[str]) -> bool:
    """Check if sub list appears as a contiguous run in target list."""
    if not sub or len(sub) > len(target):
        return False
    n = len(sub)
    return any(target[i : i + n] == sub for i in range(len(target) - n + 1))


def compile_keyword(
    keyword: str,
    mode: MatchMode,
    case_sensitive: bool,
) -> Callable[[MatchContext], str | None]:
    """Returns a matcher function: ctx -> reason string or None.

    reason is a human string like "keyword 'sl' (token)".
    Raises ValueError for invalid regex.
    Does static check for catastrophic backtracking (nested unbounded quantifiers).
    """
    original_keyword = keyword
    effective_mode = mode

    if keyword.startswith("glob:"):
        effective_mode = MatchMode.GLOB
        pattern_str = keyword[5:]
    elif keyword.startswith("regex:"):
        effective_mode = MatchMode.REGEX
        pattern_str = keyword[6:]
    else:
        pattern_str = keyword

    if effective_mode == MatchMode.REGEX:
        if check_catastrophic_regex(pattern_str):
            raise ValueError(f"catastrophic backtracking regex: {pattern_str!r}")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            rx = re.compile(pattern_str, flags)
        except re.error as e:
            raise ValueError(f"invalid regex {pattern_str!r}: {e}") from e

        reason = f"keyword '{original_keyword}' (regex)"

        def match_regex(ctx: MatchContext) -> str | None:
            # Applied to full name (max 255 chars)
            return reason if rx.search(ctx.name) else None

        return match_regex

    if effective_mode == MatchMode.GLOB:
        glob_pat = pattern_str if case_sensitive else pattern_str.casefold()
        reason = f"keyword '{original_keyword}' (glob)"

        def match_glob(ctx: MatchContext) -> str | None:
            target = ctx.name if case_sensitive else ctx.casefolded_name
            return reason if fnmatch.fnmatchcase(target, glob_pat) else None

        return match_glob

    if effective_mode == MatchMode.SUBSTRING:
        sub = pattern_str if case_sensitive else pattern_str.casefold()
        reason = f"keyword '{original_keyword}' (substring)"

        def match_substring(ctx: MatchContext) -> str | None:
            target = ctx.stem if case_sensitive else ctx.stem.casefold()
            return reason if sub in target else None

        return match_substring

    if effective_mode == MatchMode.PREFIX:
        kw_toks = keyword_tokens(pattern_str, casefold=not case_sensitive)
        prefix = (
            kw_toks[0] if kw_toks else (pattern_str if case_sensitive else pattern_str.casefold())
        )
        reason = f"keyword '{original_keyword}' (prefix)"

        def match_prefix(ctx: MatchContext) -> str | None:
            for tok in ctx.raw_toks:
                if tok.startswith(prefix):
                    return reason
            return None

        return match_prefix

    # MatchMode.TOKEN (default)
    kw_toks = keyword_tokens(pattern_str, casefold=not case_sensitive)
    if not kw_toks:
        return lambda ctx: None

    reason = f"keyword '{original_keyword}' (token)"

    def match_token(ctx: MatchContext) -> str | None:
        if _is_contiguous_subsequence(kw_toks, ctx.raw_toks):
            return reason
        if _is_contiguous_subsequence(kw_toks, ctx.camel_toks):
            return reason
        return None

    return match_token
