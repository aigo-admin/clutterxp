"""Tokenizer module for ClutterXP rule engine.

Implements Unicode NFC normalization, hidden file detection, stem/extension
splitting, raw tokenization, camelCase splitting, and keyword tokenization
per docs/02_RULE_ENGINE.md sections 2 and 3.
"""

from __future__ import annotations

import re
import unicodedata

_SEPARATOR_RE = re.compile(r"[\W_]+")
_EXT_RE = re.compile(r"^[A-Za-z0-9_+-]{1,8}$")


def normalize(name: str) -> str:
    """NFC normalize. Pure, never raises."""
    if not isinstance(name, str):
        return ""
    return unicodedata.normalize("NFC", name)


def is_hidden(name: str) -> bool:
    """True if name starts with '.'"""
    return name.startswith(".")


def split_stem(name: str) -> tuple[str, str]:
    """Returns (stem, ext) where ext is the last suffix (after stripping .tar if compound).

    Extension: 1-8 alphanumeric+_+- chars after a dot that isn't the first char.
    .tar.gz -> stem=base, ext=tar.gz. .bashrc -> stem=.bashrc, ext=''.
    """
    normalized = normalize(name)
    last_dot = normalized.rfind(".")
    if last_dot <= 0:
        return normalized, ""

    suffix = normalized[last_dot + 1 :]
    if not _EXT_RE.match(suffix):
        return normalized, ""

    stem = normalized[:last_dot]
    # Compound .tar check: if remaining stem ends with .tar (case-insensitive)
    # and that .tar also has a dot that is not the first character of stem
    if stem.lower().endswith(".tar"):
        tar_dot = stem.rfind(".")
        if tar_dot > 0:
            stem = stem[:tar_dot]
            ext = normalized[tar_dot + 1 :].lower()
            return stem, ext

    return stem, suffix.lower()


def raw_tokens(stem: str, *, casefold: bool = True) -> list[str]:
    """Split on [\\W_]+, casefold, drop empty. Pure."""
    normalized = normalize(stem)
    tokens: list[str] = []
    for piece in _SEPARATOR_RE.split(normalized):
        if not piece:
            continue
        tok = piece.casefold() if casefold else piece
        if tok:
            tokens.append(tok)
    return tokens


def _split_camel_word(token: str) -> list[str]:
    """Split a single token on camelCase / PascalCase / UPPER_to_lower boundaries.

    Letters and digits are NOT split.
    """
    if len(token) <= 1:
        return [token] if token else []

    split_indices: list[int] = [0]
    for i in range(1, len(token)):
        prev = token[i - 1]
        curr = token[i]

        # Lower-to-upper boundary: 'SecLeaf' -> 'Sec', 'Leaf'
        # Upper-run to Capital+lower: 'HTMLParser' -> 'HTML', 'Parser'
        if (prev.islower() and curr.isupper()) or (
            prev.isupper() and curr.isupper() and (i + 1 < len(token)) and token[i + 1].islower()
        ):
            split_indices.append(i)

    split_indices.append(len(token))
    return [token[split_indices[j] : split_indices[j + 1]] for j in range(len(split_indices) - 1)]


def camel_tokens(original_stem: str, *, casefold: bool = True) -> list[str]:
    """Split camelCase/PascalCase/UPPER_to_lower boundaries in original-case tokens,

    then casefold. Rules:
    - Lower-to-upper boundary: 'SecLeaf' -> ['sec', 'leaf']
    - Upper-run to Capital+lower: 'HTMLParser' -> ['html', 'parser']
    - Letters and digits are NOT split: 'sl2' stays 'sl2'
    Pure, never raises.
    """
    normalized = normalize(original_stem)
    tokens: list[str] = []
    # First split on separators without casefolding to get original-case tokens
    for piece in _SEPARATOR_RE.split(normalized):
        if not piece:
            continue
        for sub in _split_camel_word(piece):
            tok = sub.casefold() if casefold else sub
            if tok:
                tokens.append(tok)
    return tokens


def keyword_tokens(keyword: str, *, casefold: bool = True) -> list[str]:
    """Tokenize a keyword with raw algorithm only."""
    return raw_tokens(keyword, casefold=casefold)
