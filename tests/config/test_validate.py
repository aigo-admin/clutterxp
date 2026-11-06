"""Tests for cross-configuration validation (validate.py)."""

from __future__ import annotations

from pathlib import Path

from clutterxp.config.schema import Config, RuleSpec, Settings, WatchEntry
from clutterxp.config.validate import validate
from clutterxp.models import CreateDestinations, MatchMode


def test_S9_destination_inside_watched_folder_refused(tmp_path: Path) -> None:
    """S9: A destination inside a watched folder is rejected unless allow_chain: true."""
    watch_dir = tmp_path / "Downloads"
    watch_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = watch_dir / "Invoices"

    # 1. allow_chain: false -> Error
    cfg_loop = Config(
        version=1,
        settings=Settings(allow_chain=False),
        watch=[WatchEntry(path=str(watch_dir))],
        rules=[RuleSpec(name="R1", destination=str(dest_dir), keywords=["inv"])],
    )
    errors, _ = validate(cfg_loop)
    assert any("inside watched folder" in err and "allow_chain is false" in err for err in errors)

    # 2. allow_chain: true -> Permitted
    cfg_allowed = Config(
        version=1,
        settings=Settings(allow_chain=True),
        watch=[WatchEntry(path=str(watch_dir))],
        rules=[RuleSpec(name="R1", destination=str(dest_dir), keywords=["inv"])],
    )
    errors2, _ = validate(cfg_allowed)
    assert not any("inside watched folder" in err for err in errors2)


def test_validate_denylist_and_hidden_components(tmp_path: Path) -> None:
    """Denylisted paths and hidden components are rejected in destinations and watch folders."""
    # Denylisted watch folder
    cfg_bad_watch = Config(
        version=1,
        watch=[WatchEntry(path="/etc")],
        rules=[RuleSpec(name="R1", destination=str(tmp_path / "Dest"), keywords=["k"])],
    )
    errors, _ = validate(cfg_bad_watch)
    assert any("watch[0].path" in err and "system denylist" in err for err in errors)

    # Denylisted destination
    cfg_bad_dest = Config(
        version=1,
        watch=[WatchEntry(path=str(tmp_path / "Downloads"))],
        rules=[RuleSpec(name="R1", destination="/bin/my_scripts", keywords=["k"])],
    )
    errors, _ = validate(cfg_bad_dest)
    assert any("rules[0].destination" in err and "system denylist" in err for err in errors)

    # Hidden component in destination
    cfg_hidden = Config(
        version=1,
        watch=[WatchEntry(path=str(tmp_path / "Downloads"))],
        rules=[
            RuleSpec(
                name="R1",
                destination=str(tmp_path / ".secret" / "dest"),
                keywords=["k"],
            )
        ],
    )
    errors, _ = validate(cfg_hidden)
    assert any("rules[0].destination" in err and "hidden component" in err for err in errors)


def test_validate_recursive_home_refused() -> None:
    """Recursive watch of $HOME is rejected for safety."""
    cfg = Config(
        version=1,
        watch=[WatchEntry(path=str(Path.home()), recursive=True)],
        rules=[RuleSpec(name="R1", destination="/tmp/safe_dest", keywords=["k"])],
    )
    errors, _ = validate(cfg)
    assert any("watch[0].path" in err and "recursive watch of $HOME" in err for err in errors)


def test_validate_unknown_per_folder_rule(tmp_path: Path) -> None:
    """Watch entry specifying unknown rule produces error with hint."""
    cfg = Config(
        version=1,
        watch=[WatchEntry(path=str(tmp_path / "Downloads"), rules=["Invoicess"])],
        rules=[
            RuleSpec(
                name="Invoices",
                destination=str(tmp_path / "Invoices"),
                keywords=["k"],
            )
        ],
    )
    errors, _ = validate(cfg)
    assert any(
        "watch[0].rules" in err and "unknown rule 'Invoicess'" in err and "Invoices" in err
        for err in errors
    )


def test_validate_overlapping_keywords_warning(tmp_path: Path) -> None:
    """Equal priority + shared keyword + different destination triggers warning."""
    cfg_overlap = Config(
        version=1,
        rules=[
            RuleSpec(
                name="RuleA",
                priority=10,
                destination=str(tmp_path / "DestA"),
                keywords=["alpha", "shared_kw"],
            ),
            RuleSpec(
                name="RuleB",
                priority=10,
                destination=str(tmp_path / "DestB"),
                keywords=["beta", "SHARED_KW"],
            ),
        ],
    )
    _, warnings = validate(cfg_overlap)
    assert any("share keyword 'shared_kw'" in w and "RuleA" in w and "RuleB" in w for w in warnings)

    # Same destination does NOT trigger ambiguity warning
    cfg_same_dest = Config(
        version=1,
        rules=[
            RuleSpec(
                name="RuleA",
                priority=10,
                destination=str(tmp_path / "SameDest"),
                keywords=["shared_kw"],
            ),
            RuleSpec(
                name="RuleB",
                priority=10,
                destination=str(tmp_path / "SameDest"),
                keywords=["shared_kw"],
            ),
        ],
    )
    _, warnings_same = validate(cfg_same_dest)
    assert not any("ambiguity risk" in w for w in warnings_same)


def test_validate_substring_keyword_warning(tmp_path: Path) -> None:
    """Substring mode with keyword shorter than 4 characters produces warning."""
    cfg = Config(
        version=1,
        rules=[
            RuleSpec(
                name="ShortSubstring",
                match=MatchMode.SUBSTRING,
                destination=str(tmp_path / "Dest"),
                keywords=["doc", "manual"],  # "doc" is 3 chars, "manual" is 6 chars
            )
        ],
    )
    _, warnings = validate(cfg)
    assert any("substring keyword 'doc' is shorter than 4 characters" in w for w in warnings)
    assert not any("manual" in w for w in warnings)


def test_validate_missing_static_root_never_create(tmp_path: Path) -> None:
    """Missing static root produces warning when create_destinations is 'never'."""
    missing_dir = tmp_path / "non_existent_directory"
    cfg = Config(
        version=1,
        rules=[
            RuleSpec(
                name="R1",
                create_destinations=CreateDestinations.NEVER,
                destination=str(missing_dir / "{year}"),
                keywords=["k"],
            )
        ],
    )
    _, warnings = validate(cfg)
    assert any(
        "static root" in w and "does not exist and create_destinations is 'never'" in w
        for w in warnings
    )


def test_validate_warn_only(tmp_path: Path) -> None:
    """warn_only=True converts all errors into warnings."""
    cfg = Config(
        version=1,
        watch=[WatchEntry(path="/etc")],  # denylisted error
    )
    errors, warnings = validate(cfg, warn_only=True)
    assert len(errors) == 0
    assert any("system denylist" in w for w in warnings)
