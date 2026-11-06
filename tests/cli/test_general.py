"""Tests for general CLI behaviors: exit codes (A62), root refusal (S10),

safe non-UTF-8 string rendering (S12), shell completion (A67), and entry points.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from clutterxp.cli import app, cxp_main, main
from clutterxp.models import ClutterError, ErrorCode, SafetyRefusal
from tests.conftest import EnvPaths


def test_S10_root_refusal_exits_4(runner: CliRunner, tmp_path: Path) -> None:
    """Invariant S10: refuse root execution unless --allow-root is supplied (exit code 4)."""
    with patch("os.geteuid", return_value=0):
        # 1. Plain execution as root -> exit 4
        res = runner.invoke(app, ["--config", str(tmp_path / "dummy.yaml"), "scan"])
        assert res.exit_code == 4
        assert "Refused:" in res.output or "running as root" in res.output

        # 2. JSON execution as root -> valid JSON with code 4
        res_json = runner.invoke(app, ["--json", "--config", str(tmp_path / "dummy.yaml"), "scan"])
        assert res_json.exit_code == 4
        data = json.loads(res_json.output)
        assert data["code"] == 4

        # 3. With --allow-root, the root check passes
        res_allowed = runner.invoke(app, ["--allow-root", "--version"])
        assert res_allowed.exit_code == 0


def test_A62_exit_codes(runner: CliRunner, env: EnvPaths, tmp_path: Path) -> None:
    """Criterion A62: verify standard exit codes 0 through 5."""
    env.config_path.write_text("version: 1\n", encoding="utf-8")

    # Exit 0: Success
    res_0 = runner.invoke(app, ["--version"])
    assert res_0.exit_code == 0

    # Exit 1: Unexpected error
    with patch("clutterxp.cli.app.build_organizer", side_effect=RuntimeError("unexpected bug")):
        res_1 = runner.invoke(app, ["scan"])
        assert res_1.exit_code == 1

    # Exit 2: Usage / argument parsing error
    res_2 = runner.invoke(app, ["--unknown-invalid-option"])
    assert res_2.exit_code == 2

    # Exit 3: Doctor check failure (or --strict with ambiguity)
    env.config_path.unlink()
    res_3 = runner.invoke(app, ["doctor"])
    assert res_3.exit_code == 3

    # Exit 4: SafetyRefusal
    env.config_path.write_text("version: 1\n", encoding="utf-8")
    with patch(
        "clutterxp.cli.app.build_organizer",
        side_effect=SafetyRefusal("Refused", code=ErrorCode.DEST_INVALID),
    ):
        res_4 = runner.invoke(app, ["scan"])
        assert res_4.exit_code == 4

    # Exit 5: Lock contention
    with patch(
        "clutterxp.cli.app.build_organizer",
        side_effect=ClutterError("Lock timeout", code=ErrorCode.LOCK_TIMEOUT),
    ):
        res_5 = runner.invoke(app, ["scan"])
        assert res_5.exit_code == 5


def test_S12_non_utf8_filename_rendering(
    env: EnvPaths,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Invariant S12: non-UTF-8 surrogate filenames render safely without crashing."""
    src = tmp_path / "downloads"
    dest = tmp_path / "documents"
    src.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src}\n"
        f"rules:\n"
        f"  - name: all-files\n"
        f"    destination: {dest}\n",
        encoding="utf-8",
    )

    # Create a non-UTF-8 filename using raw byte decode with surrogateescape
    raw_name = b"file_\xff\xfe_test.txt".decode("utf-8", errors="surrogateescape")
    bad_file = src / raw_name
    try:
        bad_file.write_text("surrogate content")
    except OSError:
        # If filesystem doesn't support raw surrogate bytes in this environment, skip creation
        return

    # Text mode: scan should not crash
    res = runner.invoke(app, ["scan", str(src)])
    assert res.exit_code == 0
    # JSON mode: scan output should be valid JSON
    res_json = runner.invoke(app, ["--json", "scan", str(src)])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert data["command"] == "scan"


def test_A67_shell_completion(runner: CliRunner) -> None:
    """Criterion A67: shell completion support for bash, zsh, fish."""
    shells = [
        ("bash", "/bin/bash", "_clutterxp_completion"),
        ("zsh", "/bin/zsh", "_clutterxp_completion"),
        ("fish", "/usr/bin/fish", "complete --command clutterxp"),
    ]

    for shell_name, shell_path, expected_content in shells:
        with patch("shellingham.detect_shell", return_value=(shell_name, shell_path)):
            res = runner.invoke(app, ["--show-completion"])
            assert res.exit_code == 0
            assert expected_content in res.output


def test_entrypoints_callable() -> None:
    """Ensure main and cxp_main entry point callables are available."""
    assert callable(main)
    assert callable(cxp_main)
