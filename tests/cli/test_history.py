"""Tests for `clutterxp history` command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from clutterxp.cli.app import app
from clutterxp.history.service import HistoryService
from clutterxp.models import FileInfo, MoveResult, MoveStrategy
from clutterxp.paths import history_db_path
from tests.cli.conftest import validate_json_schema
from tests.conftest import EnvPaths


def _populate_history(env: EnvPaths) -> tuple[int, int]:
    service = HistoryService(history_db_path())
    batch1 = service.begin_batch("organize")
    fi1 = FileInfo(
        path=Path("/tmp/downloads/receipt.pdf"),
        name="receipt.pdf",
        stem="receipt",
        ext="pdf",
        size=1024,
        mtime_ns=1000,
        inode=1,
        device=1,
    )
    op1_id = service.begin_op(batch1, fi1.path, "Receipts", fi1)
    res1 = MoveResult(
        ok=True,
        src=fi1.path,
        dst=Path("/tmp/documents/receipt.pdf"),
        strategy=MoveStrategy.RENAME_NOREPLACE,
        created_dirs=(Path("/tmp/documents"),),
    )
    service.finish_op(op1_id, res1)
    service.record_event("move", "File moved successfully", op_id=op1_id)

    batch2 = service.begin_batch("watch")
    fi2 = FileInfo(
        path=Path("/tmp/downloads/photo.jpg"),
        name="photo.jpg",
        stem="photo",
        ext="jpg",
        size=2048,
        mtime_ns=2000,
        inode=2,
        device=1,
    )
    op2_id = service.begin_op(batch2, fi2.path, "Photos", fi2)
    res2 = MoveResult(
        ok=True,
        src=fi2.path,
        dst=Path("/tmp/pictures/photo.jpg"),
        strategy=MoveStrategy.COPY_VERIFY,
    )
    service.finish_op(op2_id, res2)
    service.mark_undone(op2_id)

    return batch1, batch2


def test_history_list_empty(env: EnvPaths, runner: CliRunner) -> None:
    res = runner.invoke(app, ["history"])
    assert res.exit_code == 0
    assert "No history operations found" in res.output


def test_history_list_json_schema(env: EnvPaths, runner: CliRunner) -> None:
    _populate_history(env)
    res = runner.invoke(app, ["--json", "history"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    validate_json_schema(data, "history.json")
    assert len(data["operations"]) == 2


def test_history_list_filters(env: EnvPaths, runner: CliRunner) -> None:
    _batch1, batch2 = _populate_history(env)

    # Filter by status
    res = runner.invoke(app, ["--json", "history", "--status", "done"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data["operations"]) == 1
    assert data["operations"][0]["status"] == "done"

    # Filter by batch
    res = runner.invoke(app, ["--json", "history", "--batch", str(batch2)])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data["operations"]) == 1
    assert data["operations"][0]["batch_id"] == batch2

    # Filter by rule
    res = runner.invoke(app, ["--json", "history", "--rule", "Receipts"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data["operations"]) == 1
    assert data["operations"][0]["rule_name"] == "Receipts"

    # Filter by path
    res = runner.invoke(app, ["--json", "history", "--path", "photo.jpg"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data["operations"]) == 1
    assert "photo.jpg" in data["operations"][0]["src_path"]

    # Invalid status
    res = runner.invoke(app, ["history", "--status", "invalid_status"])
    assert res.exit_code == 2


def test_history_show_details(env: EnvPaths, runner: CliRunner) -> None:
    _populate_history(env)
    # Check show for ID 1
    res = runner.invoke(app, ["history", "show", "1"])
    assert res.exit_code == 0
    assert "Operation #1" in res.output
    assert "receipt.pdf" in res.output

    # JSON output
    res_json = runner.invoke(app, ["--json", "history", "show", "1"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert data["operation"]["id"] == 1
    assert data["operation"]["rule_name"] == "Receipts"
    assert len(data["events"]) == 1

    # Non-existent ID
    res_missing = runner.invoke(app, ["history", "show", "99999"])
    assert res_missing.exit_code == 2


def test_history_prune(env: EnvPaths, runner: CliRunner) -> None:
    _populate_history(env)

    # Dry prompt abort
    res_abort = runner.invoke(app, ["history", "prune", "--older-than", "30d"], input="n\n")
    assert res_abort.exit_code == 0
    assert "Aborted" in res_abort.output

    # Confirmed prune with -y
    res = runner.invoke(app, ["history", "prune", "--older-than", "1d", "-y"])
    assert res.exit_code == 0
    assert "Pruned" in res.output

    # JSON prune
    res_json = runner.invoke(app, ["--json", "history", "prune", "--older-than", "1d", "-y"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert data["command"] == "history_prune"
    assert "pruned_rows" in data
