"""Tests for `clutterxp rules` subcommands proving criteria A14 and A64."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from clutterxp.cli.app import app
from tests.cli.conftest import validate_json_schema
from tests.conftest import EnvPaths


def test_A14_rules_test_explains_rules_with_json(
    env: EnvPaths,
    runner: CliRunner,
) -> None:
    """A14: rules test explains every rule for a given name, with --json."""
    runner.invoke(app, ["init"])

    # 1. Human table output
    res = runner.invoke(app, ["rules", "test", "invoice_march.pdf"])
    assert res.exit_code == 0
    assert "File: invoice_march.pdf" in res.stdout
    assert "tokens(raw)" in res.stdout
    assert "tokens(camel)" in res.stdout
    assert "Invoices" in res.stdout
    assert "Result: MATCH" in res.stdout

    # 2. JSON output validates against schema
    res_json = runner.invoke(app, ["--json", "rules", "test", "invoice_march.pdf"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    validate_json_schema(data, "rules_test.json")

    assert data["schema_version"] == 1
    assert data["file"]["name"] == "invoice_march.pdf"
    assert "invoice" in data["tokens"]["raw"]
    assert data["decision"]["kind"] == "match"
    assert data["decision"]["rule"] == "Invoices"


def test_A14_rules_test_with_real_file(
    env: EnvPaths,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """A14: rules test --file stats real file on disk."""
    runner.invoke(app, ["init"])
    test_file = tmp_path / "invoice_test.pdf"
    test_file.write_bytes(b"dummy pdf content")

    res = runner.invoke(app, ["rules", "test", "dummy.pdf", "--file", str(test_file)])
    assert res.exit_code == 0
    assert "17 bytes" in res.stdout


def test_A64_rules_add_edit_remove_preserve_comments_and_keep_bak(
    env: EnvPaths,
    runner: CliRunner,
) -> None:
    """A64: rules add/edit/remove/enable/disable preserve comments and keep a .bak."""
    runner.invoke(app, ["init"])
    orig_content = env.config_path.read_text(encoding="utf-8")
    assert "# ClutterXP example configuration" in orig_content

    # 1. Add rule
    res_add = runner.invoke(
        app,
        [
            "rules",
            "add",
            "--name",
            "TaxDocs",
            "-k",
            "tax",
            "-k",
            "w2",
            "-d",
            "~/Documents/Tax/{year}",
            "-p",
            "15",
        ],
    )
    assert res_add.exit_code == 0
    content_after_add = env.config_path.read_text(encoding="utf-8")
    # Comment preserved!
    assert "# ClutterXP example configuration" in content_after_add
    assert "TaxDocs" in content_after_add

    # Backup file kept!
    bak_path = env.config_path.with_name(env.config_path.name + ".bak")
    assert bak_path.exists()

    # 2. Edit rule
    res_edit = runner.invoke(
        app,
        ["rules", "edit", "TaxDocs", "--priority", "12", "--dest", "~/Documents/Taxes"],
    )
    assert res_edit.exit_code == 0
    content_after_edit = env.config_path.read_text(encoding="utf-8")
    assert "# ClutterXP example configuration" in content_after_edit
    assert "~/Documents/Taxes" in content_after_edit

    # 3. Disable rule
    res_disable = runner.invoke(app, ["rules", "disable", "TaxDocs"])
    assert res_disable.exit_code == 0
    content_after_disable = env.config_path.read_text(encoding="utf-8")
    assert "enabled: false" in content_after_disable

    # 4. Enable rule
    res_enable = runner.invoke(app, ["rules", "enable", "TaxDocs"])
    assert res_enable.exit_code == 0
    content_after_enable = env.config_path.read_text(encoding="utf-8")
    assert "enabled: true" in content_after_enable

    # 5. Remove rule
    res_remove = runner.invoke(app, ["rules", "remove", "TaxDocs", "--yes"])
    assert res_remove.exit_code == 0
    content_after_remove = env.config_path.read_text(encoding="utf-8")
    assert "TaxDocs" not in content_after_remove
    assert "# ClutterXP example configuration" in content_after_remove


def test_rules_add_interactive_prompts(
    env: EnvPaths,
    runner: CliRunner,
) -> None:
    """Test `rules add` prompts interactively for missing required fields."""
    runner.invoke(app, ["init"])

    # Simulate interactive user inputs: name, destination, keywords
    inputs = "Receipts\n~/Documents/Receipts\nreceipt bill\n"
    res = runner.invoke(app, ["rules", "add"], input=inputs)
    assert res.exit_code == 0
    assert "Rule 'Receipts' added successfully" in res.stdout

    content = env.config_path.read_text(encoding="utf-8")
    assert "Receipts" in content
    assert "~/Documents/Receipts" in content


def test_rules_list_and_show(env: EnvPaths, runner: CliRunner) -> None:
    """Test `rules list` and `rules show` (including --json schema validation)."""
    runner.invoke(app, ["init"])

    # List
    res_list = runner.invoke(app, ["rules", "list"])
    assert res_list.exit_code == 0
    assert "Invoices" in res_list.stdout

    res_list_json = runner.invoke(app, ["--json", "rules", "list"])
    assert res_list_json.exit_code == 0
    data = json.loads(res_list_json.stdout)
    validate_json_schema(data, "rules_list.json")

    # Show
    res_show = runner.invoke(app, ["rules", "show", "Invoices"])
    assert res_show.exit_code == 0
    assert "Rule: Invoices" in res_show.stdout

    res_show_json = runner.invoke(app, ["--json", "rules", "show", "Invoices"])
    assert res_show_json.exit_code == 0
    show_data = json.loads(res_show_json.stdout)
    assert show_data["schema_version"] == 1
    assert show_data["rule"]["name"] == "Invoices"

    # Show non-existent rule
    res_missing = runner.invoke(app, ["rules", "show", "NonExistent"])
    assert res_missing.exit_code == 2
