"""Tests for `clutterxp watch` command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from clutterxp.cli import app
from clutterxp.models import ClutterError, ErrorCode


def _setup_config_and_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    src = tmp_path / "Downloads"
    dest = tmp_path / "Documents"
    src.mkdir(parents=True)
    dest.mkdir(parents=True)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src}\n"
        f"    recursive: false\n"
        f"rules:\n"
        f"  - name: move-txt\n"
        f"    extensions: [txt]\n"
        f"    destination: {dest}\n",
        encoding="utf-8",
    )
    return cfg_file, src, dest


def test_watch_once_moves_files(runner: CliRunner, tmp_path: Path) -> None:
    cfg, src, dest = _setup_config_and_dirs(tmp_path)
    test_file = src / "notes.txt"
    test_file.write_text("hello world")

    res = runner.invoke(app, ["--config", str(cfg), "watch", "--once"])
    assert res.exit_code == 0
    assert not test_file.exists()
    assert (dest / "notes.txt").exists()


def test_watch_once_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    cfg, src, dest = _setup_config_and_dirs(tmp_path)
    test_file = src / "notes.txt"
    test_file.write_text("hello world")

    res = runner.invoke(app, ["--config", str(cfg), "watch", "--once", "--dry-run"])
    assert res.exit_code == 0
    assert test_file.exists()
    assert not (dest / "notes.txt").exists()


def test_watch_once_json_output(runner: CliRunner, tmp_path: Path) -> None:
    cfg, src, _dest = _setup_config_and_dirs(tmp_path)
    test_file = src / "notes.txt"
    test_file.write_text("hello world")

    res = runner.invoke(app, ["--json", "--config", str(cfg), "watch", "--once", "--dry-run"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["command"] == "watch"
    assert data["mode"] == "once"
    assert data["dry_run"] is True
    assert data["summary"]["will_move"] == 1


def test_watch_service_poll_flag(runner: CliRunner, tmp_path: Path) -> None:
    cfg, _src, _dest = _setup_config_and_dirs(tmp_path)

    with patch("clutterxp.cli.cmd_watch.WatchService") as mock_ws_cls:
        mock_instance = MagicMock()
        mock_ws_cls.return_value = mock_instance

        res = runner.invoke(app, ["--config", str(cfg), "watch", "--poll"])
        assert res.exit_code == 0
        mock_ws_cls.assert_called_once()
        _, kwargs = mock_ws_cls.call_args
        assert kwargs["poll"] is True
        mock_instance.run.assert_called_once()


def test_watch_lock_contention_exits_5(runner: CliRunner, tmp_path: Path) -> None:
    cfg, _src, _dest = _setup_config_and_dirs(tmp_path)

    with patch("clutterxp.cli.cmd_watch.WatchService") as mock_ws_cls:
        mock_instance = MagicMock()
        mock_instance.run.side_effect = ClutterError(
            "Watch lock already acquired", code=ErrorCode.LOCK_TIMEOUT
        )
        mock_ws_cls.return_value = mock_instance

        res = runner.invoke(app, ["--config", str(cfg), "watch"])
        assert res.exit_code == 5
