"""Tests for `clutterxp duplicates` subcommands proving criterion A60."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from clutterxp.cli.app import app
from tests.cli.conftest import validate_json_schema
from tests.conftest import EnvPaths, MakeTreeFn


def test_A60_duplicates_table_json_and_csv_output(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """A60: duplicates scan supports table, JSON, and CSV output formats."""
    scan_dir = tmp_path / "data"

    # Create two identical files (exact duplicates), plus one potential copy
    make_tree(
        {
            "data/file_a.txt": "identical content for exact duplicates",
            "data/file_b.txt": "identical content for exact duplicates",
            "data/report.pdf": "first report content",
            "data/report (1).pdf": "different content for potential duplicate",
        }
    )

    # 1. Table output (default)
    res_table = runner.invoke(app, ["duplicates", "scan", str(scan_dir)])
    assert res_table.exit_code == 0
    assert "Duplicates Scan Summary" in res_table.stdout
    assert "Exact Duplicate Groups" in res_table.stdout
    assert "Potential Duplicates" in res_table.stdout

    # 2. JSON output validates against schema
    res_json = runner.invoke(app, ["--json", "duplicates", "scan", str(scan_dir)])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    validate_json_schema(data, "duplicates.json")

    assert data["schema_version"] == 1
    assert data["summary"]["exact_groups"] == 1
    assert data["summary"]["potential_groups"] == 1
    assert len(data["exact"]) == 1
    assert len(data["potential"]) == 1

    # 3. CSV output
    res_csv = runner.invoke(
        app,
        ["duplicates", "scan", str(scan_dir), "--format", "csv"],
    )
    assert res_csv.exit_code == 0
    rows = list(csv.reader(res_csv.stdout.splitlines()))
    assert len(rows) > 1
    # Check CSV header
    assert "kind" in rows[0]
    assert "group_id" in rows[0]
    assert "path" in rows[0]


def test_A60_duplicates_output_to_file(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """A60: duplicates scan --output FILE writes results to specified file."""
    scan_dir = tmp_path / "data"
    out_file = tmp_path / "results.json"
    make_tree(
        {
            "data/doc1.txt": "same content",
            "data/doc2.txt": "same content",
        }
    )

    res = runner.invoke(
        app,
        [
            "duplicates",
            "scan",
            str(scan_dir),
            "--format",
            "json",
            "--output",
            str(out_file),
        ],
    )
    assert res.exit_code == 0
    assert out_file.exists()

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["summary"]["exact_groups"] == 1


def test_duplicates_cache_clear(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Test `duplicates cache --clear` clears hash cache database."""
    scan_dir = tmp_path / "data"
    make_tree({"data/test.txt": "content"})

    # Run scan to populate cache
    runner.invoke(app, ["duplicates", "scan", str(scan_dir)])
    assert env.cache_dir.exists()

    # Clear cache
    res = runner.invoke(app, ["duplicates", "cache", "--clear"])
    assert res.exit_code == 0
    assert "cleared" in res.stdout

    # In --json mode
    res_json = runner.invoke(app, ["--json", "duplicates", "cache", "--clear"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert data["schema_version"] == 1
    assert data["status"] == "cleared"
