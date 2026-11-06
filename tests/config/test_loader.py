"""Tests for configuration loader (loader.py)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from clutterxp.config.loader import (
    _MIGRATION_REGISTRY,
    apply_migrations,
    load,
    register_migration,
)
from clutterxp.models import ConfigError
from tests.conftest import ConfigTextFn, EnvPaths


def test_A1_user_defines_rules_nothing_built_in(env: EnvPaths, config_text: ConfigTextFn) -> None:
    """A1: User defines rules in YAML; nothing is built in."""
    content = config_text(
        rules=[
            {
                "name": "CustomDocs",
                "keywords": ["doc", "manual"],
                "destination": "~/Documents/Manuals",
                "priority": 15,
            }
        ],
        settings={"collision": "rename"},
        watch=[{"path": "~/Downloads"}],
    )
    env.config_path.write_text(content, encoding="utf-8")

    runtime = load(env.config_path)
    assert runtime.is_valid
    assert len(runtime.rules) == 1
    assert runtime.rules[0].name == "CustomDocs"
    assert runtime.rules[0].priority == 15
    assert runtime.rules[0].keywords == ["doc", "manual"]
    assert len(runtime.watch_entries) == 1
    assert runtime.settings.collision.value == "rename"


def test_A2_tilde_and_vars_expand(
    env: EnvPaths, config_text: ConfigTextFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2: ~ and $VARS expand in destinations; unresolved variable is a config error."""
    monkeypatch.setenv("MY_DOCS", "/custom/documents")

    # 1. Valid expansion with ~ and $VAR and ${VAR}
    content = config_text(
        rules=[
            {
                "name": "R1",
                "keywords": ["doc"],
                "destination": "$MY_DOCS/Subfolder",
            },
            {
                "name": "R2",
                "keywords": ["archive"],
                "destination": "${MY_DOCS}/Archive/{year}",
            },
        ]
    )
    env.config_path.write_text(content, encoding="utf-8")

    runtime = load(env.config_path)
    assert runtime.is_valid
    assert runtime.rules[0].destination == "/custom/documents/Subfolder"
    assert runtime.rules[1].destination == "/custom/documents/Archive/{year}"

    # 2. Unresolved variable is a config error
    content_bad = config_text(
        rules=[
            {
                "name": "R3",
                "keywords": ["test"],
                "destination": "$NON_EXISTENT_VAR/Folder",
            }
        ]
    )
    env.config_path.write_text(content_bad, encoding="utf-8")
    runtime_bad = load(env.config_path)
    assert not runtime_bad.is_valid
    assert any(
        "rules[0].destination" in err and "NON_EXISTENT_VAR" in err for err in runtime_bad.errors
    )


def test_A3_unknown_keys_error_with_hint(env: EnvPaths, config_text: ConfigTextFn) -> None:
    """A3: Unknown config keys are errors with a did-you-mean hint."""
    content = config_text(
        settings={"colision": "skip"},  # typo for collision
        rules=[
            {
                "name": "R1",
                "keywords": ["test"],
                "destination": "~/Test",
                "priorty": 50,  # typo for priority
            }
        ],
    )
    env.config_path.write_text(content, encoding="utf-8")

    runtime = load(env.config_path)
    assert not runtime.is_valid
    errors_str = " ".join(runtime.errors)
    assert "settings.colision" in errors_str
    assert "did you mean 'collision'" in errors_str
    assert "rules[0].priorty" in errors_str
    assert "did you mean 'priority'" in errors_str


def test_A4_config_validate_all_problems_at_once(env: EnvPaths, config_text: ConfigTextFn) -> None:
    """A4: config validate reports all problems in one run, each with its YAML path."""
    content = config_text(
        settings={"bad_setting": 123},
        watch=[{"path": ""}],  # empty path
        rules=[
            {
                "name": "R1",
                "priority": -10,  # negative priority
                "destination": "",  # empty destination
                "keywords": [""],  # empty keyword
            },
            {
                "name": "R2",
                "keywords": ["kw"],
                "destination": "~/Dest/{yearr}",  # unknown placeholder
            },
        ],
    )
    env.config_path.write_text(content, encoding="utf-8")

    runtime = load(env.config_path)
    assert not runtime.is_valid

    # All problems collected in one pass:
    paths_reported = [err.split(":")[0] for err in runtime.errors]
    assert any("settings.bad_setting" in p for p in paths_reported)
    assert any("watch[0].path" in p for p in paths_reported)
    assert any("rules[0].priority" in p for p in paths_reported)
    assert any("rules[0].destination" in p for p in paths_reported)
    assert any("rules[1].destination" in p for p in paths_reported)


def test_A5_rule_without_positive_criterion_rejected(
    env: EnvPaths, config_text: ConfigTextFn
) -> None:
    """A5: A rule with no positive criterion is rejected."""
    content = config_text(
        rules=[
            {
                "name": "OnlyExclude",
                "exclude": ["draft"],
                "destination": "~/Dest",
            }
        ]
    )
    env.config_path.write_text(content, encoding="utf-8")

    runtime = load(env.config_path)
    assert not runtime.is_valid
    assert any(
        "rules[0]" in err and "rule needs at least one of" in err.lower() for err in runtime.errors
    )


def test_load_fatal_parse_errors(env: EnvPaths) -> None:
    """Fatal parse errors (missing file, syntax error, non-dict root) raise ConfigError."""
    # 1. Non-existent file
    non_existent = env.config_path.parent / "does_not_exist.yaml"
    with pytest.raises(ConfigError, match="Config file not found"):
        load(non_existent)

    # 2. Syntax error
    env.config_path.write_text("version: 1\nsettings: [unclosed list", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML parse error"):
        load(env.config_path)

    # 3. Non-dict root
    env.config_path.write_text("- list instead of mapping", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load(env.config_path)


def test_load_version_newer_than_supported(env: EnvPaths, config_text: ConfigTextFn) -> None:
    """Version newer than supported yields upgrade error."""
    content = config_text(version=2)
    env.config_path.write_text(content, encoding="utf-8")

    runtime = load(env.config_path)
    assert not runtime.is_valid
    assert any("version" in err and "newer than supported" in err for err in runtime.errors)


def test_load_disabled_rules_separated(env: EnvPaths, config_text: ConfigTextFn) -> None:
    """Disabled rules are separated from enabled rules in RuntimeConfig."""
    content = config_text(
        rules=[
            {"name": "EnabledRule", "keywords": ["a"], "destination": "~/A", "enabled": True},
            {"name": "DisabledRule", "keywords": ["b"], "destination": "~/B", "enabled": False},
        ]
    )
    env.config_path.write_text(content, encoding="utf-8")

    runtime = load(env.config_path)
    assert runtime.is_valid
    assert len(runtime.rules) == 1
    assert runtime.rules[0].name == "EnabledRule"
    assert len(runtime.disabled_rules) == 1
    assert runtime.disabled_rules[0].name == "DisabledRule"
    assert len(runtime.all_rules) == 2


def test_migration_registry() -> None:
    """Test migration registry mechanism."""

    def migrate_1_to_2(data: dict[str, Any]) -> dict[str, Any]:
        data["migrated"] = True
        return data

    register_migration(1, migrate_1_to_2)
    try:
        data = {"version": 1, "test": "val"}
        res = apply_migrations(data, target_version=2)
        assert res["version"] == 2
        assert res["migrated"] is True
    finally:
        _MIGRATION_REGISTRY.pop(1, None)


@pytest.mark.perf
def test_perf_load_200_rules_under_50ms(env: EnvPaths, config_text: ConfigTextFn) -> None:
    """Performance budget: load 200 rules under 50ms (or under 250ms with pure-Python ruamel)."""
    rules = [
        {
            "name": f"Rule_{i:03d}",
            "priority": 100 + i,
            "keywords": [f"keyword_{i}", f"alias_{i}"],
            "extensions": ["pdf", "txt"],
            "destination": f"~/Documents/Category_{i % 10}/{{year}}",
        }
        for i in range(200)
    ]
    content = config_text(rules=rules)
    env.config_path.write_text(content, encoding="utf-8")

    # Warmup
    load(env.config_path)

    # Check for C extension
    try:
        import _ruamel_yaml  # type: ignore[import-not-found]  # noqa: F401

        has_c_ext = True
    except ImportError:
        has_c_ext = False

    budget = 0.050 if has_c_ext else 0.250

    # Timed run
    start = time.perf_counter()
    runtime = load(env.config_path)
    elapsed = time.perf_counter() - start

    assert runtime.is_valid
    assert len(runtime.rules) == 200
    assert elapsed < budget, (
        f"Loading 200 rules took {elapsed * 1000:.2f}ms (budget: {budget * 1000}ms)"
    )
