# 12: GUI (v1.1)

Built only after the CLI product (v1.0) is complete and hardened. The GUI contains **no sorting logic**.

## 1. Rules

- Toolkit: PySide6 (Qt 6), optional extra `clutterxp[gui]`; the CLI must never import it.
- The GUI talks only to the `Organizer` Protocol, `History`, and the config loader/writer, through a thin `gui/api.py` adapter.
- Every call that touches the filesystem, DB or hashes runs in a worker (`QThreadPool` / `QRunnable`); the UI thread never blocks. Workers emit Qt signals with plain dataclasses from `models.py`.
- All strings live in one place for later translation. Keyboard navigable; no information conveyed by color alone.

## 2. Windows and views

| View | Contents |
|---|---|
| **Dashboard** | watcher status (running/stopped, which folders), counts today, last 5 operations, quick buttons: Scan, Organize, Undo last batch |
| **Folders** | table of watch folders with per-folder settings (recursive, settle, ignore, rule subset); add folder by picker; start/stop watcher (controls the systemd user service if installed, otherwise an in-app watcher thread) |
| **Rules** | list with enable toggle, priority, destination; editor dialog (keywords as chips, extension picker, conditions, destination with template helper and live preview); **Test filename** box that shows the same trace as `rules test` as you type |
| **Preview** | the plan table (will move / unmatched / ambiguous / blocked), filters, checkboxes to exclude individual moves, **Apply** with confirmation; ambiguous rows have "Open rule" shortcuts |
| **History** | batches expandable to operations; search; **Undo batch / Undo file** with the exact refusal reasons from the engine |
| **Duplicates** | pick folders, run with progress and cancel; exact groups, potential groups, wasted space; open-in-file-manager; (v1.1) quarantine action |
| **Settings** | global settings, config path, validation panel (shows `config validate` output), open config in editor |

System tray icon (optional): status, pause/resume watcher, open dashboard.

## 3. Structure

```
src/clutterxp/gui/
  app.py            main(); QApplication, single-instance guard
  api.py            adapter over Organizer/History/config; returns dataclasses
  workers.py        QRunnable wrappers, cancellation tokens
  models_qt.py      QAbstractTableModel subclasses over dataclasses
  views/            one module per view
  resources/        icons, .ui-free (pure code layouts)
```

## 4. Quality bar

- `pytest-qt` tests for models and each view with a fake `api`.
- Golden test: the Preview model for a fixed fixture tree equals the CLI `--json` plan (single source of truth check).
- Offscreen CI (`QT_QPA_PLATFORM=offscreen`).
- No GUI feature may exist without the equivalent CLI/engine capability already tested.
