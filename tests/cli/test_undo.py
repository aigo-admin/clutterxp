"""Tests for `clutterxp undo` command proving criterion A30."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from clutterxp.cli.app import app
from tests.conftest import AssertUnchangedFn, EnvPaths, MakeTreeFn, SnapshotFn


def test_A30_undo_default_selector_undoes_last_batch(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """A30: undo with no selector shows last batch, confirms, and restores."""
    src_dir = tmp_path / "downloads"
    dest_dir = tmp_path / "invoices"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "downloads/invoice_1.pdf": "inv 1",
            "downloads/invoice_2.pdf": "inv 2",
        }
    )

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: Invoices\n"
        f"    keywords: [invoice]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    # 1. Organize
    res_org = runner.invoke(app, ["organize", str(src_dir), "--yes"])
    assert res_org.exit_code == 0
    assert not (src_dir / "invoice_1.pdf").exists()
    assert (dest_dir / "invoice_1.pdf").exists()

    # 2. Undo without --yes in non-interactive session exits 4
    res_no_yes = runner.invoke(app, ["undo"])
    assert (
        "confirmation required" in res_no_yes.stderr or "confirmation required" in res_no_yes.stdout
    )

    # 3. Undo with --yes succeeds and restores files
    res_undo = runner.invoke(app, ["undo", "--yes"])
    assert res_undo.exit_code == 0
    assert "RESTORE" in res_undo.stdout
    assert (src_dir / "invoice_1.pdf").exists()
    assert (src_dir / "invoice_2.pdf").exists()
    assert not (dest_dir / "invoice_1.pdf").exists()


def test_undo_dry_run_leaves_files_at_destination(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    snapshot: SnapshotFn,
    assert_unchanged: AssertUnchangedFn,
    tmp_path: Path,
) -> None:
    """Test `undo --dry-run` performs checks without moving files."""
    src_dir = tmp_path / "downloads"
    dest_dir = tmp_path / "invoices"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree({"downloads/invoice_dry.pdf": "content"})

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: Invoices\n"
        f"    keywords: [invoice]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    runner.invoke(app, ["organize", str(src_dir), "--yes"])
    assert (dest_dir / "invoice_dry.pdf").exists()

    before_dest = snapshot(dest_dir)
    res_dry = runner.invoke(app, ["undo", "--dry-run", "--yes"])
    assert res_dry.exit_code == 0
    assert "RESTORE" in res_dry.stdout
    assert "[dry-run]" in res_dry.stdout

    # File still at destination
    assert_unchanged(dest_dir, before_dest)


def test_undo_refusal_returns_exit_3(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Test undo refusal (e.g. modified file) exits 3."""
    src_dir = tmp_path / "downloads"
    dest_dir = tmp_path / "invoices"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree({"downloads/invoice_mod.pdf": "original"})

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: Invoices\n"
        f"    keywords: [invoice]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    runner.invoke(app, ["organize", str(src_dir), "--yes"])

    # Modify the destination file to trigger refusal (size/content changed)
    (dest_dir / "invoice_mod.pdf").write_bytes(b"modified content is longer")

    res = runner.invoke(app, ["undo", "--yes"])
    # Exit 3: partial failure / refusal
    assert res.exit_code == 3
    assert "REFUSE" in res.stdout

    # Waiving check with --allow-modified restores successfully
    res_allow = runner.invoke(app, ["undo", "--allow-modified", "--yes"])
    assert res_allow.exit_code == 0
    assert "RESTORE" in res_allow.stdout


def test_undo_json_mode(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Test `undo --json` emits valid JSON."""
    src_dir = tmp_path / "downloads"
    dest_dir = tmp_path / "invoices"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree({"downloads/invoice_j.pdf": "json test"})

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: Invoices\n"
        f"    keywords: [invoice]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    runner.invoke(app, ["organize", str(src_dir), "--yes"])

    res = runner.invoke(app, ["--json", "undo", "--yes"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["schema_version"] == 1
    assert data["command"] == "undo"
    assert len(data["outcomes"]) == 1
    assert data["outcomes"][0]["ok"] is True
