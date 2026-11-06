# Changelog

All notable changes are recorded here. Format: Keep a Changelog; versions: SemVer.

## [Unreleased]

## [1.1.0] - 2026-11-06

### Added
- Complete PySide6 desktop GUI application launched via `clutterxp gui` with single-instance lock (`src/clutterxp/gui/`).
- Seven dedicated GUI tabs: Dashboard (watcher state, summary stats, quick actions, event feed), Folders (watch folder configuration), Rules (rule list, interactive add/edit dialog, live trace test panel), Preview (interactive scan plan table with status filters and execute), History (operations journal with per-file and batch undo and refusal diagnosis), Duplicates (staged duplicate scanner with progress, cancellation, and quarantine action), Settings (global settings editor).
- Engine duplicate quarantine service (`src/clutterxp/duplicates/quarantine.py`) moving redundant exact duplicates into timestamped isolation directories with full journal logging and undo capability.
- Offscreen test suite verifying GUI views, models, background threading, cancellation guarantees, and CLI/Qt import isolation (`tests/gui/`).

## [1.0.0] - 2026-11-06

### Added
- Configuration engine with YAML round-trip preservation, strict schema validation, and atomic writer (`src/clutterxp/config/`).
- Rule engine with tokenization, prefix/token/substring/glob/regex matching, priority ambiguity resolution, and collision handling (`src/clutterxp/rules/`).
- Safe mover implementing atomic same-filesystem `renameat2(RENAME_NOREPLACE)` and verified copy-then-unlink across filesystems (`src/clutterxp/mover/`).
- History service with SQLite journal, transactional logging, single and batch undo, and crash recovery (`src/clutterxp/history/`).
- Watch daemon with inotify event stream, settling and debouncing, self-event suppression, and loop prevention (`src/clutterxp/watch/`).
- On-demand duplicate scanner with staged hashing (size, prefix hash, full sha256), caching, and safe quarantine (`src/clutterxp/duplicates/`).
- Complete CLI application with `init`, `rules`, `organize`, `undo`, `history`, `watch`, `duplicates`, `service`, `doctor` commands and `--json` support (`src/clutterxp/cli/`).
- Systemd user service installation, uninstallation, status inspection, and journal log viewing (`src/clutterxp/service_install.py`).
- Automated smoke verification script for README quickstart (`scripts/smoke_readme.sh`).
- CI workflows testing Python 3.10 through 3.13, cross-filesystem operations, linting, typechecking, vulnerability auditing, and clean wheel installation.
- Packaging metadata with console scripts `clutterxp` and `cxp`, wheel package data inclusion (`schema.sql`, `py.typed`, `config.example.yaml`, `packaging/clutterxp.service`), and optional CPython acceleration via `ruamel.yaml.clib`.

