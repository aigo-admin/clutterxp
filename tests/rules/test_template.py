"""Tests for template expansion and static root calculations.

Proves Acceptance Criterion A15 and Safety Invariant S13.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from clutterxp.models import ClutterError, ErrorCode, FileInfo
from clutterxp.rules.template import expand, static_root


def _make_file(
    name: str = "report.pdf",
    ext: str = "pdf",
    mtime_ns: int = 1718000000000000000,
) -> FileInfo:
    return FileInfo(
        path=Path(f"/tmp/{name}"),
        name=name,
        stem=name.rsplit(".", 1)[0] if "." in name else name,
        ext=ext,
        size=1024,
        mtime_ns=mtime_ns,
        inode=100,
        device=1,
    )


def test_static_root() -> None:
    assert static_root("/home/user/Invoices/{year}/{month}") == "/home/user/Invoices"
    assert static_root("/home/user/Invoices/{year}") == "/home/user/Invoices"
    assert static_root("~/Documents/{ext}") == "~/Documents"
    assert static_root("/fixed/path/no/placeholder") == "/fixed/path/no/placeholder"
    assert static_root("/{year}") == "/"
    assert static_root("{year}") == ""


def test_A15_template_expansion_basic() -> None:
    # A15: {year}/{month}, {ext}, {rule}, {mtime:%Y-%m} expand correctly
    file = _make_file("document.pdf", ext="pdf", mtime_ns=1700000000000000000)
    environ = {"HOME": "/home/alice", "BACKUP_ROOT": "/mnt/backups"}

    # {year} and {month}
    dest1 = expand("~/Invoices/{year}/{month}", file, "Invoices", environ=environ)
    assert str(dest1).startswith("/home/alice/Invoices/")
    assert len(dest1.parts) >= 5

    # {ext} and {rule}
    dest2 = expand(
        "$BACKUP_ROOT/{rule}/{ext}",
        file,
        "WorkDocuments",
        environ=environ,
    )
    assert dest2 == Path("/mnt/backups/WorkDocuments/pdf")

    # {mtime:%Y-%m}
    dest3 = expand("/data/{mtime:%Y-%m}", file, "Monthly", environ=environ)
    assert str(dest3).startswith("/data/")
    # Must match YYYY-MM
    date_part = dest3.name
    assert len(date_part) == 7 and date_part[4] == "-"


def test_A15_template_ext_noext_fallback() -> None:
    file = _make_file("Makefile", ext="")
    dest = expand("/tmp/sorted/{ext}", file, "MakeRule")
    assert dest == Path("/tmp/sorted/noext")


def test_A15_mtime_slash_rejected() -> None:
    # A15: {mtime:} with '/' in format string is rejected -> DEST_INVALID
    file = _make_file()
    with pytest.raises(ClutterError) as exc_info:
        expand("/dest/{mtime:%Y/%m}", file, "Rule")
    assert exc_info.value.code == ErrorCode.DEST_INVALID


def test_A15_unknown_placeholder_with_did_you_mean() -> None:
    file = _make_file()
    with pytest.raises(ClutterError) as exc_info:
        expand("/dest/{yearr}", file, "Rule")
    assert exc_info.value.code == ErrorCode.DEST_INVALID
    assert "year" in str(exc_info.value)  # Did you mean 'year'?


def test_A15_unresolved_env_var_rejected() -> None:
    file = _make_file()
    with pytest.raises(ClutterError) as exc_info:
        expand("$NONEXISTENT_VAR_12345/docs", file, "Rule", environ={})
    assert exc_info.value.code == ErrorCode.DEST_INVALID


def test_A13_A15_dot_dot_in_values_rejected() -> None:
    # A13/A15: '..' and '/' in values are rejected -> DEST_INVALID
    file = _make_file(ext="..")
    with pytest.raises(ClutterError) as exc_info:
        expand("/dest/{ext}", file, "Rule")
    assert exc_info.value.code == ErrorCode.DEST_INVALID


def test_A15_slash_in_rule_sanitized() -> None:
    # Rule names with slashes are sanitized by stripping path separators
    file = _make_file()
    dest = expand("/dest/{rule}", file, "Project/Subproject")
    assert dest == Path("/dest/ProjectSubproject")


def test_S13_cannot_escape_static_root() -> None:
    # S13: Template-expanded destinations cannot escape their static root
    file = _make_file()
    with pytest.raises(ClutterError) as exc_info:
        # A template where path after placeholder tries to escape static root
        expand("/home/alice/docs/{year}/../../etc", file, "Rule")
    assert exc_info.value.code == ErrorCode.DEST_INVALID
    assert "escapes static root" in str(exc_info.value)


def test_template_day_and_mtime_default() -> None:
    file = _make_file(mtime_ns=1700000000000000000)
    dest_day = expand("/dest/{day}", file, "Rule")
    assert dest_day.parent == Path("/dest")

    dest_mtime = expand("/dest/{mtime}", file, "Rule")
    assert dest_mtime.parent == Path("/dest")


def test_template_invalid_destination_not_absolute() -> None:
    file = _make_file()
    with pytest.raises(ClutterError) as exc_info:
        expand("relative/path/{ext}", file, "Rule")
    assert exc_info.value.code == ErrorCode.DEST_INVALID


def test_template_nul_character_rejected() -> None:
    file = _make_file(ext="bad\x00ext")
    with pytest.raises(ClutterError) as exc_info:
        expand("/dest/{ext}", file, "Rule")
    assert exc_info.value.code == ErrorCode.DEST_INVALID


def test_template_tilde_fallback_home_env(monkeypatch: pytest.MonkeyPatch) -> None:
    file = _make_file()
    monkeypatch.delenv("HOME", raising=False)
    dest = expand("~/test_docs", file, "Rule", environ={})
    assert dest.is_absolute()


@given(st.text(max_size=20))
def test_hypothesis_no_substitutions_escape_or_corrupt(safe_word: str) -> None:
    file = _make_file()
    # If a rule name contains '/' or '..' or NUL, it should either be sanitized or rejected
    try:
        dest = expand("/tmp/dest/{rule}", file, safe_word)
        # Result must be absolute and normalized
        assert dest.is_absolute()
        assert ".." not in dest.parts
    except ClutterError as e:
        assert e.code == ErrorCode.DEST_INVALID
