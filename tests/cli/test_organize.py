"""Tests for `clutterxp organize` command proving criteria A17 and A69."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from clutterxp.cli.app import app
from tests.cli.conftest import validate_json_schema
from tests.conftest import AssertUnchangedFn, EnvPaths, MakeTreeFn, SnapshotFn


def test_A17_organize_dry_run_leaves_tree_byte_identical(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    snapshot: SnapshotFn,
    assert_unchanged: AssertUnchangedFn,
    tmp_path: Path,
) -> None:
    """A17 / Invariant S6: organize --dry-run leaves the user tree byte-identical."""
    src_dir = tmp_path / "downloads"
    dest_dir = tmp_path / "documents" / "invoices"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "downloads/invoice_1.pdf": "invoice one content",
            "downloads/invoice_2.pdf": "invoice two content",
            "downloads/notes.txt": "regular text notes",
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

    before_src = snapshot(src_dir)
    before_dest = snapshot(dest_dir)

    res = runner.invoke(app, ["organize", str(src_dir), "--dry-run"])
    assert res.exit_code == 0
    assert "MOVE" in res.stdout
    assert "Nothing was changed (dry run)." in res.stdout

    # Assert tree is byte-identical
    assert_unchanged(src_dir, before_src)
    assert_unchanged(dest_dir, before_dest)


def test_A69_bulk_apply_confirmation_threshold_non_tty_exits_4(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """A69: moves >= confirm_threshold without a TTY and without --yes exits 4."""
    src_dir = tmp_path / "downloads"
    dest_dir = tmp_path / "sorted"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Create 5 files
    files = {f"downloads/doc_{i}.pdf": f"content {i}" for i in range(5)}
    make_tree(files)

    # Set confirm_threshold to 3 (so 5 moves exceeds threshold)
    env.config_path.write_text(
        f"version: 1\n"
        f"settings:\n"
        f"  confirm_threshold: 3\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: Docs\n"
        f"    keywords: [doc]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    # In non-interactive test runner without --yes: exits 4
    res = runner.invoke(app, ["organize", str(src_dir)])
    assert res.exit_code == 4
    assert "confirmation required" in res.stderr or "confirmation required" in res.stdout

    # With --yes: succeeds with exit code 0
    res_yes = runner.invoke(app, ["organize", str(src_dir), "--yes"])
    assert res_yes.exit_code == 0
    assert "Summary: 5 moved" in res_yes.stdout
    assert "Undo with: clutterxp undo --batch" in res_yes.stdout


def test_organize_apply_and_json_schema(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Test organize --json output matches schema."""
    src_dir = tmp_path / "downloads"
    dest_dir = tmp_path / "invoices"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "downloads/invoice_a.pdf": "content a",
            "downloads/photo.jpg": "image content",
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

    res = runner.invoke(app, ["--json", "organize", str(src_dir), "--yes"])
    assert res.exit_code == 0

    data = json.loads(res.stdout)
    validate_json_schema(data, "organize.json")
    assert data["schema_version"] == 1
    assert data["command"] == "organize"
    assert data["dry_run"] is False
    assert data["summary"]["moved"] == 1
    assert data["summary"]["unmatched"] == 1
    assert data["batch_id"] is not None


def test_organize_limit_and_rule_filter(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Test organize with --limit and --rule flags."""
    src_dir = tmp_path / "downloads"
    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    dest1.mkdir(parents=True, exist_ok=True)
    dest2.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "downloads/rule1_a.txt": "a",
            "downloads/rule1_b.txt": "b",
            "downloads/rule2_c.txt": "c",
        }
    )

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: Rule1\n"
        f"    keywords: [rule1]\n"
        f"    destination: {dest1}\n"
        f"  - name: Rule2\n"
        f"    keywords: [rule2]\n"
        f"    destination: {dest2}\n",
        encoding="utf-8",
    )

    # 1. Test --rule filter: only moves rule1
    res = runner.invoke(app, ["organize", str(src_dir), "--rule", "Rule1", "--yes"])
    assert res.exit_code == 0
    assert (dest1 / "rule1_a.txt").exists()
    assert (dest1 / "rule1_b.txt").exists()
    # rule2_c was not moved because Rule2 was filtered out
    assert (src_dir / "rule2_c.txt").exists()

    # 2. Test --limit 1: moves only 1 file
    res_limit = runner.invoke(app, ["organize", str(src_dir), "--limit", "1", "--yes"])
    assert res_limit.exit_code == 0
    assert (dest2 / "rule2_c.txt").exists()
