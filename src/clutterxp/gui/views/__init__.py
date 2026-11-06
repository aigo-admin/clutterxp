"""GUI Views package for ClutterXP."""

from __future__ import annotations

from .dashboard import DashboardView
from .duplicates import DuplicatesView
from .folders import FoldersView
from .history import HistoryView
from .preview import PreviewView
from .rules import RulesView
from .settings import SettingsView

__all__ = [
    "DashboardView",
    "DuplicatesView",
    "FoldersView",
    "HistoryView",
    "PreviewView",
    "RulesView",
    "SettingsView",
]
