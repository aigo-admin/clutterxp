"""Tests for `clutterxp doctor` command proving criterion A65."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from clutterxp.cli.app import app
from clutterxp.history.service import HistoryService
from clutterxp.models import FileInfo
from clutterxp.paths import history_db_path
from tests.cli.conftest import validate_json_schema
from tests.conftest import EnvPaths


def _setup_valid_env(env: EnvPaths, tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "downloads"
    dest = tmp_path / "documents"
    src.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src}\n"
        f"rules:\n"
        f"  - name: test-rule\n"
        f"    extensions: [pdf]\n"
        f"    destination: {dest}\n",
        encoding="utf-8",
    )
    return src, dest


def test_A65_doctor_clean_state(env: EnvPaths, runner: CliRunner, tmp_path: Path) -> None:
    _setup_valid_env(env, tmp_path)

    # Text output
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 0
    assert "ClutterXP Doctor Diagnostics" in res.output

    # JSON output schema
    res_json = runner.invoke(app, ["--json", "doctor"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    validate_json_schema(data, "doctor.json")
    assert data["ok"] is True
    check_names = [c["name"] for c in data["checks"]]
    assert "config_validity" in check_names
    assert "database_integrity" in check_names
    assert "pending_operations" in check_names


def test_A65_doctor_detects_unreconciled_pending_ops(
    env: EnvPaths,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    _setup_valid_env(env, tmp_path)
    service = HistoryService(history_db_path())
    batch_id = service.begin_batch("organize")
    fi = FileInfo(
        path=tmp_path / "downloads" / "file.pdf",
        name="file.pdf",
        stem="file",
        ext="pdf",
        size=10,
        mtime_ns=1000,
        inode=10,
        device=1,
    )
    service.begin_op(batch_id, fi.path, "test-rule", fi)

    # Without --fix, doctor fails with exit code 3
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 3
    assert "un-reconciled pending operations" in res.output

    # JSON output reflects failure
    res_json = runner.invoke(app, ["--json", "doctor"])
    assert res_json.exit_code == 3
    data = json.loads(res_json.output)
    assert data["ok"] is False
    pending_check = next(c for c in data["checks"] if c["name"] == "pending_operations")
    assert pending_check["status"] == "error"


def test_A65_doctor_fix_reconciles_and_cleans(
    env: EnvPaths,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    src, dest = _setup_valid_env(env, tmp_path)
    service = HistoryService(history_db_path())
    batch_id = service.begin_batch("organize")
    fi = FileInfo(
        path=src / "file.pdf",
        name="file.pdf",
        stem="file",
        ext="pdf",
        size=10,
        mtime_ns=1000,
        inode=10,
        device=1,
    )
    service.begin_op(batch_id, fi.path, "test-rule", fi)

    # Also create a stale partial file older than 1h
    stale_file = dest / "sample.clutterxp-partial"
    stale_file.write_text("partial data")
    two_hours_ago = time.time() - 7200
    os.utime(stale_file, (two_hours_ago, two_hours_ago))

    # Run with --fix
    res_fix = runner.invoke(app, ["--json", "doctor", "--fix"])
    assert res_fix.exit_code == 0
    data = json.loads(res_fix.output)
    assert data["ok"] is True
    assert len(data["fixes"]) >= 2
    assert not stale_file.exists()


def test_doctor_detects_missing_config(env: EnvPaths, runner: CliRunner) -> None:
    if env.config_path.exists():
        env.config_path.unlink()

    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 3
    assert "Config file not found" in res.output


def test_doctor_detects_unwritable_destination(
    env: EnvPaths,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    _src, dest = _setup_valid_env(env, tmp_path)
    dest.chmod(0o444)

    try:
        # Doctor detects destination permission issue
        res = runner.invoke(app, ["doctor"])
        assert res.exit_code == 3
        assert "Destination access issues" in res.output
    finally:
        dest.chmod(0o755)

    # Now passes
    res_ok = runner.invoke(app, ["doctor"])
    assert res_ok.exit_code == 0
