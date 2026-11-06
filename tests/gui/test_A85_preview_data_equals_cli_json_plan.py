"""Test A85: Preview data equals the CLI --json plan for the same fixture (golden test)."""

from __future__ import annotations

import json

from pytestqt.qtbot import QtBot
from typer.testing import CliRunner

from clutterxp.cli.app import app
from clutterxp.gui.api import GuiApi
from clutterxp.gui.views.preview import PreviewView
from tests.conftest import EnvPaths, MakeTreeFn


def test_A85_preview_data_equals_cli_json_plan(
    qtbot: QtBot,
    gui_api: GuiApi,
    env: EnvPaths,
    make_tree: MakeTreeFn,
) -> None:
    # 1. Setup fixture files in the watched Downloads folder
    watch_dir = env.config_path.parent / "Downloads"
    watch_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "invoice_march.pdf": "invoice march content",
            "bank_statement.pdf": "statement content",
            "receipt_lunch.png": "receipt png content",
            "random_notes.txt": "just plain text notes",
        },
        base=watch_dir,
    )

    # 2. Run CLI command: clutterxp --json organize <watch_dir> --dry-run
    runner = CliRunner()
    cli_res = runner.invoke(app, ["--json", "organize", str(watch_dir), "--dry-run"])
    assert cli_res.exit_code == 0, f"CLI organize failed: {cli_res.output}"

    cli_json = json.loads(cli_res.output)
    assert cli_json["command"] == "organize"
    assert cli_json["dry_run"] is True
    cli_actions = cli_json["actions"]

    # 3. Generate plan in GUI PreviewView
    view = PreviewView(gui_api)
    qtbot.addWidget(view)
    view.path_edit.setText(str(watch_dir))
    view.scan()

    qtbot.waitUntil(lambda: view._model.rowCount() == len(cli_actions), timeout=5000)

    # 4. Compare every row in Preview model against CLI actions
    cli_by_src = {act["src"]: act for act in cli_actions}

    for row_idx in range(view._model.rowCount()):
        gui_act = view._model.get_action(row_idx)
        assert gui_act is not None

        src_str = str(gui_act.file.path)
        assert src_str in cli_by_src, f"Unexpected file in GUI plan: {src_str}"
        cli_act = cli_by_src[src_str]

        # Verify status
        assert gui_act.status.value == cli_act["status"]

        # Verify destination
        if "dst" in cli_act:
            assert str(gui_act.dest_path) == cli_act["dst"]
        else:
            assert gui_act.dest_path is None

        # Verify rule hit
        if "rule" in cli_act:
            assert gui_act.decision.hit is not None
            assert gui_act.decision.hit.rule_name == cli_act["rule"]
            assert gui_act.decision.hit.matched_by == cli_act["matched_by"]
        else:
            assert gui_act.decision.hit is None
