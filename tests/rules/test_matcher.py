"""Tests for keyword compilation and matching modes.

Proves Acceptance Criteria A7, A8, A9 and catastrophic regex detection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clutterxp.config.schema import RuleSpec, Settings
from clutterxp.models import DecisionKind, FileInfo, MatchMode
from clutterxp.rules.engine import RuleEngineImpl
from clutterxp.rules.matcher import (
    MatchContext,
    check_catastrophic_regex,
    compile_keyword,
)
from clutterxp.rules.tokenizer import camel_tokens, raw_tokens, split_stem


def _make_ctx(filename: str, *, case_sensitive: bool = False) -> MatchContext:
    stem, ext = split_stem(filename)
    return MatchContext(
        name=filename,
        casefolded_name=filename.casefold(),
        stem=stem,
        ext=ext,
        raw_toks=raw_tokens(stem, casefold=not case_sensitive),
        camel_toks=camel_tokens(stem, casefold=not case_sensitive),
        case_sensitive=case_sensitive,
    )


def test_A7_token_mode_sl_matches_sl_ctf_not_slides() -> None:
    # A7: Alias 'sl' matches 'SL-CTF.pdf' but never 'slides.pdf'
    matcher = compile_keyword("sl", MatchMode.TOKEN, case_sensitive=False)

    ctx_sl = _make_ctx("SL-CTF.pdf")
    ctx_slides = _make_ctx("slides.pdf")

    res_sl = matcher(ctx_sl)
    assert res_sl is not None
    assert "keyword 'sl' (token)" in res_sl

    res_slides = matcher(ctx_slides)
    assert res_slides is None


def test_A8_glob_mode_matches_full_filename() -> None:
    # A8: glob: keywords work on full file names
    matcher = compile_keyword("glob:*-ctf-*.pdf", MatchMode.GLOB, case_sensitive=False)

    assert matcher(_make_ctx("SL-ctf-writeup.pdf")) is not None
    assert matcher(_make_ctx("SL-ctf-writeup.docx")) is None
    assert matcher(_make_ctx("random.pdf")) is None


def test_A8_regex_mode_matches_full_filename() -> None:
    # A8: regex: keywords work on full file names
    matcher = compile_keyword("regex:^report_\\d{4}\\.pdf$", MatchMode.REGEX, case_sensitive=False)

    assert matcher(_make_ctx("report_2024.pdf")) is not None
    assert matcher(_make_ctx("REPORT_1999.PDF")) is not None
    assert matcher(_make_ctx("my_report_2024.pdf")) is None


def test_A8_invalid_regex_raises_error() -> None:
    # A8: invalid regex is an error
    with pytest.raises(ValueError, match="invalid regex"):
        compile_keyword("regex:[unclosed_bracket", MatchMode.REGEX, case_sensitive=False)


def test_A8_catastrophic_backtracking_regex_rejected() -> None:
    # Catastrophic backtracking detection
    with pytest.raises(ValueError, match="catastrophic backtracking"):
        compile_keyword("regex:(a+)+", MatchMode.REGEX, case_sensitive=False)

    with pytest.raises(ValueError, match="catastrophic backtracking"):
        compile_keyword("regex:([a-zA-Z0-9]+)+", MatchMode.REGEX, case_sensitive=False)

    with pytest.raises(ValueError, match="catastrophic backtracking"):
        compile_keyword("regex:(a*)*", MatchMode.REGEX, case_sensitive=False)

    with pytest.raises(ValueError, match="catastrophic backtracking"):
        compile_keyword("regex:(a+){2,}", MatchMode.REGEX, case_sensitive=False)


def test_check_catastrophic_regex_safe_patterns() -> None:
    # Safe patterns should return False
    assert check_catastrophic_regex(r"^report_\d{4}\.pdf$") is False
    assert check_catastrophic_regex(r"(a+)?") is False
    assert check_catastrophic_regex(r"(a|b)+") is False
    assert check_catastrophic_regex(r"[a-z]+") is False
    assert check_catastrophic_regex(r"(a{1,3})+") is False


def test_A9_compound_extension_tar_gz_case_insensitive() -> None:
    # A9: Extension filters work, case-insensitive, including compound tar.gz
    rule = RuleSpec(
        name="Archives",
        destination="/dest/archives",
        extensions=["tar.gz"],
    )
    engine = RuleEngineImpl([rule], Settings())

    for fname in ("data.tar.gz", "DATA.TAR.GZ", "data.Tar.Gz"):
        stem, ext = split_stem(fname)
        file = FileInfo(
            path=Path(f"/tmp/{fname}"),
            name=fname,
            stem=stem,
            ext=ext,
            size=1024,
            mtime_ns=1700000000000000000,
            inode=1,
            device=1,
        )
        dec = engine.decide(file)
        assert dec.kind == DecisionKind.MATCH, f"Failed for {fname}"
        assert dec.hit is not None
        assert dec.hit.rule_name == "Archives"


def test_prefix_mode() -> None:
    matcher = compile_keyword("sec", MatchMode.PREFIX, case_sensitive=False)
    assert matcher(_make_ctx("SecLeafNotes.pdf")) is not None
    assert matcher(_make_ctx("notes_sec.pdf")) is not None
    assert matcher(_make_ctx("unrelated.pdf")) is None


def test_empty_tokens_keyword_matcher() -> None:
    matcher = compile_keyword("---", MatchMode.TOKEN, case_sensitive=False)
    assert matcher(_make_ctx("document.pdf")) is None


def test_case_sensitive_matching() -> None:
    matcher = compile_keyword("SL", MatchMode.TOKEN, case_sensitive=True)
    assert matcher(_make_ctx("SL-CTF.pdf", case_sensitive=True)) is not None
    assert matcher(_make_ctx("sl-ctf.pdf", case_sensitive=True)) is None

    matcher_sub = compile_keyword("Inv", MatchMode.SUBSTRING, case_sensitive=True)
    assert matcher_sub(_make_ctx("MyInvoice.pdf", case_sensitive=True)) is not None
    assert matcher_sub(_make_ctx("myinvoice.pdf", case_sensitive=True)) is None

    matcher_glob = compile_keyword("glob:*.PDF", MatchMode.GLOB, case_sensitive=True)
    assert matcher_glob(_make_ctx("doc.PDF", case_sensitive=True)) is not None
    assert matcher_glob(_make_ctx("doc.pdf", case_sensitive=True)) is None


def test_check_catastrophic_named_groups_and_lookaround() -> None:
    assert check_catastrophic_regex(r"(?P<name>a+)+") is True
    assert check_catastrophic_regex(r"(?:a+)+") is True
    assert check_catastrophic_regex(r"(?=a+)+") is True
