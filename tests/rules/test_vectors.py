"""Normative test vectors from docs/02_RULE_ENGINE.md section 5 and property tests.

Proves Acceptance Criterion A6.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from clutterxp.config.schema import RuleSpec, Settings
from clutterxp.models import DecisionKind, FileInfo, MatchMode
from clutterxp.rules.engine import RuleEngineImpl
from clutterxp.rules.matcher import MatchContext, compile_keyword
from clutterxp.rules.tokenizer import camel_tokens, normalize, raw_tokens, split_stem

VECTORS = [
    ("SecLeaf_notes.pdf", "secleaf", MatchMode.TOKEN, True, "raw tokens [secleaf, notes]"),
    ("SecLeaf_notes.pdf", "sl", MatchMode.TOKEN, False, "no token equals sl"),
    ("SL-CTF-writeup.pdf", "sl", MatchMode.TOKEN, True, "raw [sl, ctf, writeup]"),
    ("SL-CTF-writeup.pdf", "sl-ctf", MatchMode.TOKEN, True, "contiguous [sl, ctf]"),
    ("SL-CTF-writeup.pdf", "ctf-sl", MatchMode.TOKEN, False, "wrong order"),
    ("slides.pdf", "sl", MatchMode.TOKEN, False, "token is slides"),
    ("tcpdump_capture.pcap", "tc", MatchMode.TOKEN, False, "token is tcpdump"),
    ("TC_report (1).docx", "tc", MatchMode.TOKEN, True, "raw [tc, report, 1]"),
    ("report-tc-final.docx", "tc", MatchMode.TOKEN, True, "token in the middle"),
    (
        "SecLeafNotes.pdf",
        "secleaf",
        MatchMode.TOKEN,
        False,
        "raw [secleafnotes], camel [sec, leaf, notes]",
    ),
    ("SecLeafNotes.pdf", "sec leaf", MatchMode.TOKEN, True, "camel stream contiguous run"),
    ("SecLeafNotes.pdf", "secleaf", MatchMode.PREFIX, True, "raw token starts with it"),
    ("HTMLParser.py", "html", MatchMode.TOKEN, True, "camel [html, parser]"),
    ("sl2_notes.txt", "sl", MatchMode.TOKEN, False, "digits not split: sl2"),
    ("invoice.2024.03.pdf", "invoice", MatchMode.TOKEN, True, "dots separate tokens"),
    ("x-ctf-y.pdf", "glob:*-ctf-*", MatchMode.GLOB, True, "fnmatch on full name"),
    ("Report_2024.pdf", "regex:^report_\\d{4}", MatchMode.REGEX, True, "re.IGNORECASE"),
]


@pytest.mark.parametrize("filename,keyword,mode,expected_match,reason", VECTORS)
def test_A6_tokenizer_test_vectors(
    filename: str,
    keyword: str,
    mode: MatchMode,
    expected_match: bool,
    reason: str,
) -> None:
    stem, ext = split_stem(filename)
    ctx = MatchContext(
        name=filename,
        casefolded_name=filename.casefold(),
        stem=stem,
        ext=ext,
        raw_toks=raw_tokens(stem),
        camel_toks=camel_tokens(stem),
        case_sensitive=False,
    )
    matcher = compile_keyword(keyword, mode, case_sensitive=False)
    matched = matcher(ctx) is not None
    assert matched == expected_match, f"Failed on {filename} with kw {keyword}: {reason}"


def test_A6_test_vector_compound_extension() -> None:
    # backup.TAR.GZ | ext filter tar.gz | ✓ | compound, case-insensitive
    filename = "backup.TAR.GZ"
    stem, ext = split_stem(filename)
    assert stem == "backup"
    assert ext == "tar.gz"

    rule = RuleSpec(
        name="Backup",
        destination="/dest/backups",
        extensions=["tar.gz"],
    )
    engine = RuleEngineImpl([rule], Settings())
    file = FileInfo(
        path=Path(f"/tmp/{filename}"),
        name=filename,
        stem=stem,
        ext=ext,
        size=1024,
        mtime_ns=1700000000000000000,
        inode=1,
        device=1,
    )
    decision = engine.decide(file)
    assert decision.kind == DecisionKind.MATCH
    assert decision.hit is not None
    assert decision.hit.rule_name == "Backup"


def test_A6_test_vector_tar_stripped_stem() -> None:
    # archive.tar.gz | stem | archive | .tar stripped as well
    stem, ext = split_stem("archive.tar.gz")
    assert stem == "archive"
    assert ext == "tar.gz"


def test_A6_test_vector_hidden_file() -> None:
    # .bashrc | anything | IGNORED | hidden
    filename = ".bashrc"
    stem, ext = split_stem(filename)
    rule = RuleSpec(
        name="Any",
        destination="/dest/any",
        keywords=["bashrc"],
    )
    engine = RuleEngineImpl([rule], Settings(skip_hidden=True))
    file = FileInfo(
        path=Path(f"/tmp/{filename}"),
        name=filename,
        stem=stem,
        ext=ext,
        size=1024,
        mtime_ns=1700000000000000000,
        inode=1,
        device=1,
    )
    decision = engine.decide(file)
    assert decision.kind == DecisionKind.IGNORED
    assert "hidden" in decision.reason


def test_A6_test_vector_nfd_normalization() -> None:
    # résumé_final.pdf (NFD form) | résumé (NFC) | ✓ | NFC normalization
    nfd_filename = unicodedata.normalize("NFD", "résumé_final.pdf")
    nfc_keyword = unicodedata.normalize("NFC", "résumé")
    stem, ext = split_stem(nfd_filename)
    ctx = MatchContext(
        name=nfd_filename,
        casefolded_name=nfd_filename.casefold(),
        stem=stem,
        ext=ext,
        raw_toks=raw_tokens(stem),
        camel_toks=camel_tokens(stem),
        case_sensitive=False,
    )
    matcher = compile_keyword(nfc_keyword, MatchMode.TOKEN, case_sensitive=False)
    assert matcher(ctx) is not None


def test_A6_test_vector_exclude_wins() -> None:
    # draft_invoice.pdf | rule keywords invoice, exclude draft | rule does not match | exclude wins
    filename = "draft_invoice.pdf"
    stem, ext = split_stem(filename)
    rule = RuleSpec(
        name="Invoices",
        destination="/dest/invoices",
        keywords=["invoice"],
        exclude=["draft"],
    )
    engine = RuleEngineImpl([rule], Settings())
    file = FileInfo(
        path=Path(f"/tmp/{filename}"),
        name=filename,
        stem=stem,
        ext=ext,
        size=1024,
        mtime_ns=1700000000000000000,
        inode=1,
        device=1,
    )
    decision = engine.decide(file)
    assert decision.kind == DecisionKind.NO_MATCH


# Property tests using Hypothesis
@given(st.text())
def test_hypothesis_normalization_idempotent(text: str) -> None:
    n1 = normalize(text)
    n2 = normalize(n1)
    assert n1 == n2


@given(st.text())
def test_hypothesis_tokenizer_never_raises(text: str) -> None:
    stem, ext = split_stem(text)
    assert isinstance(stem, str)
    assert isinstance(ext, str)
    r_toks = raw_tokens(stem)
    c_toks = camel_tokens(stem)
    assert isinstance(r_toks, list)
    assert isinstance(c_toks, list)


@given(st.text())
def test_hypothesis_raw_tokens_never_empty_string(text: str) -> None:
    toks = raw_tokens(text)
    for t in toks:
        assert len(t) > 0


def test_A6_decide_is_deterministic() -> None:
    rule = RuleSpec(
        name="Invoices",
        destination="/dest/invoices/{year}",
        keywords=["invoice"],
        priority=10,
    )
    engine = RuleEngineImpl([rule], Settings())
    file = FileInfo(
        path=Path("/tmp/invoice_2024.pdf"),
        name="invoice_2024.pdf",
        stem="invoice_2024",
        ext="pdf",
        size=2048,
        mtime_ns=1700000000000000000,
        inode=42,
        device=1,
    )
    d1 = engine.decide(file)
    d2 = engine.decide(file)
    assert d1 == d2
