"""Filename normalization for potential-duplicate grouping.

Strips only the closed list of numeric and copy patterns from docs/07 section 3.
Does not perform fuzzy matching.
"""

from __future__ import annotations

import re

# Closed list of copy-pattern suffixes to strip (from docs/07 section 3):
# ' (1)', '(2)', ' - copy', ' copy', '_copy', '-copy', '_1', '-1'
# Applied to the STEM only, not the extension.
# 'report2.pdf' stays 'report2' (digit NOT part of copy pattern unless space/dash before)
COPY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s+\(\d+\)$"),
    re.compile(r"\(\d+\)$"),
    re.compile(r"\s+-\s+copy$"),
    re.compile(r"\s+copy$"),
    re.compile(r"_copy$"),
    re.compile(r"-copy$"),
    re.compile(r"_\d+$"),
    re.compile(r"-\d+$"),
)


def normalize_stem(stem: str) -> str:
    """Casefold and strip copy markers. Only the patterns in the closed list.

    Idempotent: strips until no more patterns match.
    """
    stem = stem.casefold()
    while True:
        matched = False
        for pat in COPY_PATTERNS:
            m = pat.search(stem)
            if m is not None:
                new_stem = stem[: m.start()]
                if new_stem:  # do not strip down to empty string
                    stem = new_stem
                    matched = True
                    break
        if not matched:
            break
    return stem
