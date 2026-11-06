"""Modern, minimalist, elegant dark theme for ClutterXP GUI.

Provides a unified design system with clean typography, balanced spacing,
sleek card surfaces, modern button hierarchy, and custom scrollbars.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

MODERN_STYLESHEET = """
/* ====================================================================
   ClutterXP Modern Minimalist Theme (Obsidian & Indigo)
   ==================================================================== */

* {
    outline: none;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}

QMainWindow, QDialog, QWidget {
    background-color: #0f1117;
    color: #f1f5f9;
    font-size: 13px;
}

/* ==========================================
   Tab Widget & Segmented Tab Bar
   ========================================== */
QTabWidget::pane {
    border: 1px solid #1e2433;
    border-radius: 8px;
    background-color: #121620;
    padding: 6px;
    margin-top: -1px;
}

QTabBar {
    background-color: transparent;
    qproperty-drawBase: 0;
}

QTabBar::tab {
    background-color: #171b26;
    color: #94a3b8;
    border: 1px solid #262d3d;
    border-radius: 6px;
    padding: 7px 18px;
    margin: 3px 5px 3px 0px;
    font-size: 13px;
    font-weight: 500;
}

QTabBar::tab:hover {
    background-color: #1f2637;
    color: #f8fafc;
    border-color: #3b455c;
}

QTabBar::tab:selected {
    background-color: #6366f1;
    color: #ffffff;
    border: 1px solid #818cf8;
    font-weight: 600;
}

/* ==========================================
   Cards & Group Boxes
   ========================================== */
QGroupBox {
    background-color: #161a25;
    border: 1px solid #232a3b;
    border-radius: 8px;
    margin-top: 20px;
    padding: 16px 14px 14px 14px;
    font-weight: 600;
    font-size: 13px;
    color: #f8fafc;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    top: 2px;
    padding: 0 6px;
    background-color: #161a25;
    color: #a5b4fc;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* ==========================================
   Buttons
   ========================================== */
QPushButton {
    background-color: #1e2433;
    color: #f1f5f9;
    border: 1px solid #2d374d;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
    font-size: 13px;
    min-height: 22px;
}

QPushButton:hover {
    background-color: #273044;
    border-color: #404f6d;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #181d29;
    border-color: #6366f1;
}

QPushButton:disabled {
    background-color: #141720;
    color: #475569;
    border-color: #1d222e;
}

/* Specialized Button Variants via Property */
QPushButton[primary="true"] {
    background-color: #6366f1;
    color: #ffffff;
    border: 1px solid #818cf8;
    font-weight: 600;
}

QPushButton[primary="true"]:hover {
    background-color: #4f46e5;
    border-color: #a5b4fc;
}

QPushButton[primary="true"]:pressed {
    background-color: #4338ca;
}

QPushButton[success="true"] {
    background-color: #10b981;
    color: #ffffff;
    border: 1px solid #34d399;
    font-weight: 600;
}

QPushButton[success="true"]:hover {
    background-color: #059669;
    border-color: #6ee7b7;
}

QPushButton[danger="true"] {
    background-color: #e11d48;
    color: #ffffff;
    border: 1px solid #fb7185;
    font-weight: 600;
}

QPushButton[danger="true"]:hover {
    background-color: #be123c;
    border-color: #fda4af;
}

/* ==========================================
   Input Controls
   ========================================== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #0c0f16;
    border: 1px solid #232a3b;
    border-radius: 6px;
    padding: 6px 10px;
    color: #f8fafc;
    font-size: 13px;
    selection-background-color: #6366f1;
    selection-color: #ffffff;
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #6366f1;
    background-color: #0f131d;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox QAbstractItemView {
    background-color: #161a25;
    border: 1px solid #232a3b;
    border-radius: 6px;
    selection-background-color: #6366f1;
    selection-color: #ffffff;
    color: #f1f5f9;
    padding: 4px;
}

/* ==========================================
   Tables & Lists
   ========================================== */
QTableView, QListWidget, QTextEdit {
    background-color: #0c0f16;
    alternate-background-color: #11151f;
    border: 1px solid #232a3b;
    border-radius: 6px;
    gridline-color: #1a202d;
    color: #f1f5f9;
    selection-background-color: #2e384e;
    selection-color: #ffffff;
    font-size: 13px;
}

QHeaderView::section {
    background-color: #161a25;
    color: #94a3b8;
    font-weight: 600;
    font-size: 12px;
    padding: 7px 8px;
    border: none;
    border-bottom: 1px solid #232a3b;
    border-right: 1px solid #1a202d;
}

QHeaderView::section:last {
    border-right: none;
}

/* ==========================================
   Progress Bar
   ========================================== */
QProgressBar {
    border: 1px solid #232a3b;
    border-radius: 6px;
    text-align: center;
    background-color: #0c0f16;
    color: #f8fafc;
    font-weight: 600;
    font-size: 11px;
    height: 16px;
}

QProgressBar::chunk {
    background-color: #6366f1;
    border-radius: 5px;
}

/* ==========================================
   Scroll Bars
   ========================================== */
QScrollBar:vertical {
    background: #0c0f16;
    width: 8px;
    margin: 0;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #2b3345;
    min-height: 24px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #475569;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: #0c0f16;
    height: 8px;
    margin: 0;
    border-radius: 4px;
}

QScrollBar::handle:horizontal {
    background: #2b3345;
    min-width: 24px;
    border-radius: 4px;
}

QScrollBar::handle:horizontal:hover {
    background: #475569;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* ==========================================
   Status Bar, Menu Bar & Tooltips
   ========================================== */
QStatusBar {
    background-color: #0c0f16;
    border-top: 1px solid #1e2433;
    color: #94a3b8;
    font-size: 12px;
    padding: 4px 8px;
}

QMenuBar {
    background-color: #0f1117;
    border-bottom: 1px solid #1e2433;
    color: #cbd5e1;
    padding: 2px 4px;
}

QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: #1e2433;
    color: #f8fafc;
}

QMenu {
    background-color: #161a25;
    border: 1px solid #232a3b;
    border-radius: 6px;
    padding: 4px;
}

QMenu::item {
    padding: 6px 20px;
    border-radius: 4px;
    color: #f1f5f9;
}

QMenu::item:selected {
    background-color: #6366f1;
    color: #ffffff;
}

QMenu::separator {
    height: 1px;
    background-color: #232a3b;
    margin: 4px 8px;
}

QToolTip {
    background-color: #1e2433;
    color: #f8fafc;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}
"""


def apply_theme(app: QApplication) -> None:
    """Apply the modern dark theme and palette to the QApplication."""
    # Palette configuration
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#0f1117"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f1f5f9"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#0c0f16"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#121620"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1e2433"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f1f5f9"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1e2433"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f1f5f9"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#6366f1"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))

    app.setPalette(palette)
    app.setStyleSheet(MODERN_STYLESHEET)
