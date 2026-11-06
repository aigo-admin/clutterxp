"""Unit and property tests for tokenizer module."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from clutterxp.rules.tokenizer import (
    camel_tokens,
    is_hidden,
    keyword_tokens,
    normalize,
    raw_tokens,
    split_stem,
)


def test_nfc_normalization() -> None:
    # NFD string: e + combining acute accent
    nfd_str = "re\u0301sume\u0301"
    nfc_str = "résumé"
    assert normalize(nfd_str) == nfc_str
    assert normalize("hello") == "hello"
    assert normalize("") == ""
    # Test non-string defensive handling
    assert normalize(123) == ""  # type: ignore[arg-type]


def test_hidden_file_detection() -> None:
    assert is_hidden(".bashrc") is True
    assert is_hidden(".profile") is True
    assert is_hidden(".config") is True
    assert is_hidden(".") is True
    assert is_hidden("..") is True
    assert is_hidden("file.txt") is False
    assert is_hidden("regular_name") is False


def test_split_stem_basic() -> None:
    assert split_stem("file.pdf") == ("file", "pdf")
    assert split_stem("document.docx") == ("document", "docx")
    assert split_stem("Makefile") == ("Makefile", "")
    assert split_stem(".bashrc") == (".bashrc", "")
    assert split_stem(".bashrc.bak") == (".bashrc", "bak")
    assert split_stem("no_ext.") == ("no_ext.", "")


def test_split_stem_compound_tar() -> None:
    assert split_stem("archive.tar.gz") == ("archive", "tar.gz")
    assert split_stem("backup.TAR.GZ") == ("backup", "tar.gz")
    assert split_stem("x.tar.bz2") == ("x", "tar.bz2")
    assert split_stem("data.tar.xz") == ("data", "tar.xz")
    # Single tar
    assert split_stem("bundle.tar") == ("bundle", "tar")


def test_split_stem_extension_length_limits() -> None:
    # 1 to 8 characters allowed for extension
    assert split_stem("file.c") == ("file", "c")
    assert split_stem("file.12345678") == ("file", "12345678")
    # 9 chars -> not treated as extension
    assert split_stem("file.123456789") == ("file.123456789", "")


def test_raw_tokens_separators() -> None:
    # Test vector stems
    assert raw_tokens("SecLeaf_notes") == ["secleaf", "notes"]
    assert raw_tokens("SL-CTF-writeup") == ["sl", "ctf", "writeup"]
    assert raw_tokens("slides") == ["slides"]
    assert raw_tokens("tcpdump_capture") == ["tcpdump", "capture"]
    assert raw_tokens("TC_report (1)") == ["tc", "report", "1"]
    assert raw_tokens("report-tc-final") == ["report", "tc", "final"]
    assert raw_tokens("SecLeafNotes") == ["secleafnotes"]
    assert raw_tokens("HTMLParser") == ["htmlparser"]
    assert raw_tokens("sl2_notes") == ["sl2", "notes"]
    assert raw_tokens("invoice.2024.03") == ["invoice", "2024", "03"]
    assert raw_tokens("") == []
    assert raw_tokens("---___...") == []


def test_camel_tokens_boundaries() -> None:
    # Lower-to-upper boundary
    assert camel_tokens("SecLeaf") == ["sec", "leaf"]
    assert camel_tokens("SecLeafNotes") == ["sec", "leaf", "notes"]
    assert camel_tokens("secLeaf") == ["sec", "leaf"]

    # Upper-run to Capital+lower
    assert camel_tokens("HTMLParser") == ["html", "parser"]
    assert camel_tokens("XMLHttpRequest") == ["xml", "http", "request"]
    assert camel_tokens("IOError") == ["io", "error"]

    # Letters and digits NOT split
    assert camel_tokens("sl2") == ["sl2"]
    assert camel_tokens("v2") == ["v2"]
    assert camel_tokens("sl2_notes") == ["sl2", "notes"]
    assert camel_tokens("sl2Notes") == ["sl2notes"]

    # Separators combined with camel
    assert camel_tokens("SecLeaf_notes") == ["sec", "leaf", "notes"]
    assert camel_tokens("SL-CTF-writeup") == ["sl", "ctf", "writeup"]


def test_keyword_tokens() -> None:
    assert keyword_tokens("sl-ctf") == ["sl", "ctf"]
    assert keyword_tokens("sec leaf") == ["sec", "leaf"]
    assert keyword_tokens("invoice") == ["invoice"]
    assert keyword_tokens("SecLeaf") == ["secleaf"]  # raw algorithm only, no camel splitting


@given(st.text())
def test_hypothesis_normalize_never_raises(s: str) -> None:
    res = normalize(s)
    assert isinstance(res, str)


@given(st.text())
def test_hypothesis_raw_tokens_no_empty_tokens(s: str) -> None:
    toks = raw_tokens(s)
    for t in toks:
        assert t != ""
        assert t == t.casefold()
