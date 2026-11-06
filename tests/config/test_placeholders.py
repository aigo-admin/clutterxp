"""Tests for destination template placeholders and static root extraction."""

from __future__ import annotations

from clutterxp.config.placeholders import (
    ALLOWED_PLACEHOLDERS,
    extract_static_root,
    validate_destination_template,
)


def test_allowed_placeholders_set() -> None:
    assert "year" in ALLOWED_PLACEHOLDERS
    assert "month" in ALLOWED_PLACEHOLDERS
    assert "day" in ALLOWED_PLACEHOLDERS
    assert "ext" in ALLOWED_PLACEHOLDERS
    assert "rule" in ALLOWED_PLACEHOLDERS
    assert "mtime" in ALLOWED_PLACEHOLDERS


def test_extract_static_root() -> None:
    assert extract_static_root("~/Documents/Invoices/{year}/{month}") == "~/Documents/Invoices"
    assert extract_static_root("/tmp/{rule}/files") == "/tmp"
    assert extract_static_root("{year}/sub") == "."
    assert extract_static_root("/{year}/sub") == "/"
    assert extract_static_root("/home/user/Downloads") == "/home/user/Downloads"
    assert extract_static_root("") == "."


def test_validate_destination_template_valid() -> None:
    assert validate_destination_template("~/Docs/{year}/{month}/{day}/{ext}/{rule}") == []
    assert validate_destination_template("~/Docs/{mtime:%Y-%m-%d}") == []
    assert validate_destination_template("~/Docs/{mtime}") == []


def test_validate_destination_template_invalid() -> None:
    # Unmatched brackets
    errs = validate_destination_template("~/Docs/{year")
    assert any("Unmatched brackets" in e for e in errs)

    # Empty placeholder
    errs = validate_destination_template("~/Docs/{}/files")
    assert any("Empty placeholder" in e for e in errs)

    # Unknown placeholder with did-you-mean
    errs = validate_destination_template("~/Docs/{yearr}")
    assert any("did you mean '{year}'" in e for e in errs)

    # Unknown placeholder without close match
    errs = validate_destination_template("~/Docs/{completely_unknown_xyz}")
    assert any("Unknown placeholder" in e for e in errs)

    # Invalid mtime format with slash
    errs = validate_destination_template("~/Docs/{mtime:%Y/%m}")
    assert any("cannot contain '/'" in e for e in errs)

    # Empty mtime format
    errs = validate_destination_template("~/Docs/{mtime:}")
    assert any("Empty mtime format" in e for e in errs)
