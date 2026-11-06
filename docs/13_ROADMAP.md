# 13: Roadmap, Milestones and Agent Waves

## 1. Milestones

| ID | Name | Scope | Exit criteria |
|---|---|---|---|
| **M0** | Scaffold | repo layout, tooling, frozen contracts, CI | `pip install -e .[dev]`, `pytest` runs (empty), `ruff`, `mypy` green |
| **M1** | Core | config + validation, rule engine (tokenizer, matchers, templates), scanner, planner, safe mover, Organizer `plan/apply`, CLI `init/config/rules/scan/organize` | acceptance A1 to A25 in `15_ACCEPTANCE.md` |
| **M2** | History | SQLite journal, `history`, batches, `undo`, recovery | A26 to A40 |
| **M3** | Watch | watcher, settle, per-folder config, reload, signals, `watch` command | A41 to A52 |
| **M4** | Duplicates | staged pipeline, cache, potential groups, reports | A53 to A60 |
| **M5** | Product | `--json` everywhere, `doctor`, `service`, completion, packaging, docs | A61 to A70 |
| **M6** | Hardening, **v1.0** | QA integration suite, fault injection sweep, security review, perf budgets | A71 to A80 and CI green |
| **M7** | GUI, **v1.1** | PySide6 app, duplicate quarantine | A81 to A90 |

## 2. Agent waves (parallel execution plan)

Dependencies decide the waves. Agents in the same wave never edit the same files (ownership table in `AGENTS.md`).

| Wave | Agents (run in parallel) | Depends on |
|---|---|---|
| **W0** | A00 Scaffold + test harness (or the Orchestrator itself): git repo, subpackage skeletons, fixtures/fakes, contract-drift guard | none |
| **W1** | A01 Config, A02 Rules, A03 Mover, A04 History, A05 Duplicates | frozen contracts only |
| **W2** | A06 Organizer + Scanner + Planner, A07 Undo + Recovery, A08 Watcher | W1 merged (or coded against Protocol fakes) |
| **W3** | A09 CLI, A10 Packaging + Service + CI | W2 merged |
| **W4** | A11 QA/Integration, A12 Security review (no source edits, files findings) | W3 merged |
| **W5** | A13 GUI | v1.0 tagged |

Agent prompts: `prompts/A00_scaffold_harness.md` through `prompts/A13_gui.md`; orchestrator: `prompts/00_ORCHESTRATOR.md`.

Merge order inside a wave: contracts first, then leaf modules, then integrators. After every wave the Orchestrator runs the full test suite and `ruff`/`mypy` before releasing the next wave.

## 3. Backlog (not scheduled)

- Desktop notifications on move/ambiguity (`notify-send`).
- Rule packs: export/import a rule subset as a single YAML file.
- Opt-in magic-byte type detection (reads first bytes only) for extension spoofing.
- `duplicates quarantine` (v1.1, specified in `07`).
- macOS support (FSEvents via watchdog; no `renameat2`; use `link`/`rename` fallback; no `/proc` open-file check, use `lsof`).
- Debian package for Kali repositories.
- Shell prompt integration showing watcher state.
- Optional per-rule "action: copy" (copy instead of move). Needs its own undo semantics (delete the copy is deletion, so it would need quarantine instead).

## 4. Explicitly out of scope

AI/ML classification, content analysis, cloud sync, automatic deletion, telemetry, remote control.
