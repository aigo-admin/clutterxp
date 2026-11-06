"""Tests for `clutterxp scan` command proving criterion A16."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from clutterxp.cli.app import app
from tests.cli.conftest import validate_json_schema
from tests.conftest import AssertUnchangedFn, EnvPaths, MakeTreeFn, SnapshotFn


def test_A16_scan_classifies_and_writes_nothing(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    snapshot: SnapshotFn,
    assert_unchanged: AssertUnchangedFn,
    tmp_path: Path,
) -> None:
    """A16: scan classifies files as match/unmatched/ambiguous/blocked and writes nothing."""
    src_dir = tmp_path / "downloads"
    dest_dir = tmp_path / "documents" / "invoices"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Pre-existing file at destination to cause collision/block
    (dest_dir / "invoice_march.pdf").write_bytes(b"existing invoice")

    # Source files:
    # 1. invoice_march.pdf -> blocked by collision
    # 2. invoice_april.pdf -> will_move
    # 3. photo.jpg -> unmatched
    # 4. tc_sl_notes.txt -> ambiguous (with 2 equal priority rules)
    make_tree(
        {
            "downloads/invoice_march.pdf": "march invoice",
            "downloads/invoice_april.pdf": "april invoice",
            "downloads/photo.jpg": "photo data",
            "downloads/ambig_doc.txt": "ambiguous content",
        }
    )

    # Configure rules
    env.config_path.write_text(
        f"version: 1\n"
        f"settings:\n"
        f"  collision: skip\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: Invoices\n"
        f"    priority: 10\n"
        f"    keywords: [invoice]\n"
        f"    destination: {dest_dir}\n"
        f"  - name: AmbigRuleA\n"
        f"    priority: 20\n"
        f"    keywords: [ambig]\n"
        f"    destination: {tmp_path}/destA\n"
        f"  - name: AmbigRuleB\n"
        f"    priority: 20\n"
        f"    keywords: [ambig]\n"
        f"    destination: {tmp_path}/destB\n",
        encoding="utf-8",
    )

    before_src = snapshot(src_dir)
    before_dest = snapshot(dest_dir)

    # Run scan
    res = runner.invoke(app, ["scan", str(src_dir)])
    assert res.exit_code == 0
    assert "MOVE" in res.stdout
    assert "KEEP" in res.stdout
    assert "BLOCK" in res.stdout
    assert "AMBIG" in res.stdout

    # Invariant S6: Zero writes to user folders during scan
    assert_unchanged(src_dir, before_src)
    assert_unchanged(dest_dir, before_dest)


def test_A16_scan_json_validates_schema(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """A16 / A61: scan --json outputs valid schema structure."""
    src_dir = tmp_path / "downloads"
    make_tree(
        {
            "downloads/invoice_may.pdf": "may invoice",
            "downloads/notes.txt": "text notes",
        }
    )
    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: Invoices\n"
        f"    keywords: [invoice]\n"
        f"    destination: {tmp_path}/invoices\n",
        encoding="utf-8",
    )

    res = runner.invoke(app, ["--json", "scan", str(src_dir)])
    assert res.exit_code == 0

    data = json.loads(res.stdout)
    validate_json_schema(data, "scan.json")
    assert data["schema_version"] == 1
    assert data["command"] == "scan"
    assert data["dry_run"] is True
    assert data["summary"]["matched"] == 1
    assert data["summary"]["unmatched"] == 1


def test_A16_scan_strict_on_ambiguity(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """A16 / 08_CLI: scan --strict returns exit code 3 if any file was ambiguous."""
    src_dir = tmp_path / "downloads"
    make_tree({"downloads/ambig_file.txt": "ambig content"})

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: Rule1\n"
        f"    priority: 10\n"
        f"    keywords: [ambig]\n"
        f"    destination: {tmp_path}/dest1\n"
        f"  - name: Rule2\n"
        f"    priority: 10\n"
        f"    keywords: [ambig]\n"
        f"    destination: {tmp_path}/dest2\n",
        encoding="utf-8",
    )

    # Without strict: returns 0
    res_normal = runner.invoke(app, ["scan", str(src_dir)])
    assert res_normal.exit_code == 0

    # With strict: returns 3
    res_strict = runner.invoke(app, ["scan", str(src_dir), "--strict"])
    assert res_strict.exit_code == 3
