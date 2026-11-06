"""Test A84: Rules editor includes live 'test filename' trace and template preview."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from clutterxp.gui.api import GuiApi
from clutterxp.gui.views.rules import RuleEditorDialog, RulesView


def test_A84_rules_editor_includes_live_trace(
    qtbot: QtBot,
    gui_api: GuiApi,
) -> None:
    view = RulesView(gui_api)
    qtbot.addWidget(view)

    # Wait for rules to load into model
    qtbot.waitUntil(lambda: view._rules_model.rowCount() >= 2, timeout=5000)

    # 1. Live test filename trace
    # Test matching filename for 'Invoices' rule
    view.test_input.setText("march_invoice.pdf")
    qtbot.waitUntil(lambda: view._trace_model.rowCount() >= 2, timeout=5000)

    traces = [view._trace_model._traces[i] for i in range(view._trace_model.rowCount())]
    invoice_trace = next(t for t in traces if t.rule_name == "Invoices")
    assert invoice_trace.matched is True
    assert invoice_trace.priority == 10

    # Test non-matching filename
    view.test_input.setText("random_vacation.txt")
    qtbot.waitUntil(lambda: all(not t.matched for t in view._trace_model._traces), timeout=5000)
    for t in view._trace_model._traces:
        assert t.matched is False

    # 2. RuleEditorDialog with template helper and live preview
    dialog = RuleEditorDialog(None)
    qtbot.addWidget(dialog)

    dialog.name_edit.setText("Receipts")
    dialog.keywords_edit.setText("receipt, store")
    dialog.extensions_edit.setText("pdf, png")
    dialog.dest_edit.setText("~/Receipts/{year}/{month}")

    # Check live destination preview
    assert "Receipts/" in dialog.dest_preview_label.text()
    assert "{year}" not in dialog.dest_preview_label.text()

    # Click template chip button to insert {day}
    dialog._insert_placeholder("/{day}")
    assert "{day}" not in dialog.dest_preview_label.text()

    dialog._on_save()
    assert dialog.result() == 1  # Accepted
    saved_spec = dialog.get_rule_spec()
    assert saved_spec.name == "Receipts"
    assert "receipt" in saved_spec.keywords

    # 3. Add rule via api and verify table updates
    gui_api.config_save_rule(saved_spec, is_new=True)
    view.refresh()
    qtbot.waitUntil(lambda: view._rules_model.rowCount() == 3, timeout=5000)
    rule_names = [
        r.name
        for i in range(view._rules_model.rowCount())
        if (r := view._rules_model.get_rule(i)) is not None
    ]
    assert "Receipts" in rule_names
