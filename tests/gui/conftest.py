"""Fixtures for ClutterXP GUI test suite."""

from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from clutterxp.config.writer import init_config
from clutterxp.gui.api import GuiApi
from tests.conftest import EnvPaths, MakeTreeFn


@pytest.fixture
def gui_api(env: EnvPaths, make_tree: MakeTreeFn) -> GuiApi:
    """Create GuiApi with an initialized configuration and sample folders."""
    init_config(env.config_path, minimal=True, force=True)

    # Configure a sample watch folder and destination
    watch_dir = env.config_path.parent / "Downloads"
    dest_dir = env.config_path.parent / "Documents" / "Invoices"
    watch_dir.mkdir(parents=True, exist_ok=True)
    dest_dir.mkdir(parents=True, exist_ok=True)

    yaml = YAML()
    with env.config_path.open("r", encoding="utf-8") as f:
        doc = yaml.load(f)

    doc["watch"] = [
        {
            "path": str(watch_dir),
            "recursive": False,
            "settle_seconds": 3.0,
            "rules": "all",
        }
    ]
    doc["rules"] = [
        {
            "name": "Invoices",
            "priority": 10,
            "keywords": ["invoice", "inv", "receipt"],
            "extensions": ["pdf", "png"],
            "destination": str(dest_dir),
        },
        {
            "name": "Statements",
            "priority": 20,
            "keywords": ["statement", "bank"],
            "extensions": ["pdf"],
            "destination": str(env.config_path.parent / "Documents" / "Statements"),
        },
    ]

    with env.config_path.open("w", encoding="utf-8") as f:
        yaml.dump(doc, f)

    return GuiApi(config_path=env.config_path)
