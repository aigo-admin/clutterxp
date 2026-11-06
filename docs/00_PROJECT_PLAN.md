# ClutterXP: Project Plan v0.2

| | |
|---|---|
| Tool name | **ClutterXP** (command: `clutterxp`, short alias `cxp`, Python package `clutterxp`) |
| Supersedes | `Clutter` plan v0.1 |
| Platform | Linux first (developed and tested on Kali; nothing is Kali-specific), portable design |
| Interfaces | CLI first (full product), GUI second (v1.1) |
| Language | Python 3.10+ |

## 1. Purpose

ClutterXP is a configurable, lightweight, local file organizer. The user defines **rules** (keywords, aliases, extensions, size/age conditions, destinations). ClutterXP then sorts files from **any folders the user chooses** (Downloads is only the most common one) either on demand or automatically in the background.

Nothing is built in. No category, keyword, or folder is hardcoded. Names such as "SecLeaf", "ThunderCipher" or "College" are only examples a user may configure.

## 2. Non-negotiable principles

1. **Explicit match or nothing.** A file moves only when a configured rule explicitly matches. No match: the file stays exactly where it is. No automatic "Unsorted" folder.
2. **Never guess.** Equal-priority conflicts are reported as *ambiguous*, not resolved silently.
3. **Never lose data.** No automatic deletion. A move never overwrites. The source is removed only after the destination is verified.
4. **Reversible.** Every move is journaled and can be undone, singly or per batch.
5. **Preview first.** Anything bulk can be previewed; bulk applies ask for confirmation.
6. **Cheap.** No content reading or hashing during sorting. Event-driven watching, no polling or periodic full scans.
7. **Local and private.** No network access, no telemetry, DB and logs are `0600`.
8. **One engine.** CLI, watcher and GUI all call the same `Organizer` facade.

## 3. Folders are fully custom

- Any folder can be organized or watched: `clutterxp organize ~/Desktop`, `clutterxp watch /mnt/data/inbox`.
- Folders can be listed in config (`watch:` section) with **per-folder settings**: recursive on/off, extra ignore patterns, settle time, and an optional **subset of rules** that apply to that folder.
- Files can arrive in a folder by download, copy, drag-and-drop, or being moved in by another program. The watcher handles `created`, `moved-in` and `closed-after-write` events uniformly.
- Ad-hoc folders never need to be in config: a path argument always wins.

## 4. What changed from v0.1 (review findings, all adopted)

| # | Finding | Resolution in v0.2 |
|---|---|---|
| 1 | Token matching undefined | Precise tokenizer, match modes, test vectors: `docs/02_RULE_ENGINE.md` |
| 2 | `priority` contradicts "ambiguous" | Lower number wins; ambiguity only at equal best priority with different destinations |
| 3 | Move atomicity and cross-filesystem safety | No-clobber atomic primitive and verified copy protocol: `docs/04_SAFE_MOVER.md` |
| 4 | Download completion vague, and not only Downloads | Settle algorithm for any folder, moved-in files, open-for-write check: `docs/06_WATCHER.md` |
| 5 | Undo/history integrity | Journal-first protocol, crash recovery, batches, verification: `docs/05_HISTORY_UNDO.md` |
| 6 | Config validation | Pydantic schema, strict keys, comment-preserving edits, JSON Schema export: `docs/03_CONFIG.md` |
| 7 | Safety gaps | Invariants, loop prevention, root refusal, lock, system-dir denylist: `docs/09_SAFETY.md` |

## 5. Feature set (prioritized for efficiency)

**P0: core product (v1.0 cannot ship without these)**

| Feature | Why it earns its place |
|---|---|
| Rules: keywords, `all_of`, `exclude`, extensions, size, age | Covers real sorting needs with metadata that is already in `stat()` (free) |
| Safe token matching + `glob:` / `regex:` escape hatches | Avoids the `sl` matches `slides` class of bugs |
| Priority + ambiguity detection | Predictable, explainable outcomes |
| `rules test <filename>` | Debug rules in seconds; pure function, almost free to build |
| `scan` / `organize --dry-run` / `organize` | Preview before acting |
| Safe mover (no-clobber, verified cross-FS) | The riskiest code; gets the most tests |
| History, batch IDs, `undo` (op / batch / file) | Trust: everything reversible |
| `watch` with settle detection | Automatic sorting for any folder |
| Config validation + `config validate` + `doctor` | Fail early with clear messages |
| Lock file + root refusal + system-dir denylist | Safety invariants |

**P1: included in v1.0 (cheap, high value)**

| Feature | Notes |
|---|---|
| Destination templates `{year}` `{month}` `{ext}` `{mtime:%Y-%m}` | Sub-sorting without extra rules |
| Per-folder watch config + rule subsets | Makes "any folder" real |
| Built-in + custom ignore patterns | Browser temp files, torrent clients, dotfiles |
| Collision policy per rule: `skip` / `rename` | Defined behavior instead of "don't overwrite" |
| `--json` output on every command | Scripting; also the GUI test oracle |
| Duplicate scan: size, then partial hash, then full SHA-256, with hash cache | Fast on re-runs; never runs during sorting |
| `service install` (systemd `--user`) | Real background operation |
| `init`, `config schema`, shell completion | Onboarding; editor autocomplete via JSON Schema |
| Crash recovery (`doctor --fix`) | Pending journal reconciliation |

**P2: after v1.0**

| Feature | Release |
|---|---|
| PySide6 GUI (same engine) | v1.1 |
| `duplicates quarantine` (move, never delete, undoable, confirmed) | v1.1 |
| Desktop notifications (`notify-send`) | v1.2 |
| Rule import/export packs | v1.2 |
| Optional magic-byte type detection (opt-in, reads first bytes only) | v1.2 |
| Other OS support (macOS first) | v2.0 |

**Rejected on purpose:** AI/content classification, fuzzy filename matching, auto-delete, cloud sync, telemetry. Each adds cost or risk that contradicts the principles above.

## 6. Milestones

See `docs/13_ROADMAP.md`. Short form:

| Milestone | Delivers |
|---|---|
| M1 Core | config, rule engine, scanner, planner, safe mover, `scan/organize/rules` |
| M2 History | SQLite journal, `history`, `undo`, batches, recovery |
| M3 Watch | watcher, settle detection, per-folder config, reload |
| M4 Duplicates | staged hashing, cache, reports |
| M5 Product | CLI polish, `--json`, service, `doctor`, packaging |
| M6 Hardening | QA, fault injection, security review, perf budgets. **v1.0** |
| M7 GUI | PySide6 app. **v1.1** |

## 7. Document map

```
README.md                  what is in this package and how to start
STARTER_PROMPT.md          paste-ready multi-agent kickoff prompt
AGENTS.md / CLAUDE.md      standing rules for every AI agent
docs/00_PROJECT_PLAN.md    this file
docs/01_ARCHITECTURE.md    layers, data flow, threading, contracts
docs/02_RULE_ENGINE.md     matching semantics and test vectors
docs/03_CONFIG.md          schema, validation, locations, edits
docs/04_SAFE_MOVER.md      atomic move and cross-FS protocol
docs/05_HISTORY_UNDO.md    SQLite schema, journaling, undo, recovery
docs/06_WATCHER.md         events, settle, loop prevention, signals
docs/07_DUPLICATES.md      staged duplicate detection
docs/08_CLI.md             every command, flag, output, exit code
docs/09_SAFETY.md          invariants, threat model, denylist
docs/10_TESTING.md         strategy, fixtures, fault injection, CI
docs/11_PACKAGING.md       pyproject, pipx, systemd, Kali notes
docs/12_GUI.md             PySide6 design (v1.1)
docs/13_ROADMAP.md         milestones, waves, backlog
docs/14_DECISIONS.md       decision log with rationale
docs/15_ACCEPTANCE.md      numbered acceptance criteria per milestone
docs/16_RISKS.md           risk register
prompts/                   orchestrator + one prompt per agent
src/clutterxp/models.py    FROZEN data contracts
src/clutterxp/interfaces.py FROZEN module interfaces
src/clutterxp/config/schema.py  FROZEN config field names/types (Pydantic)
src/clutterxp/units.py     size/duration parsing shared by config and rules
config.example.yaml        annotated example configuration
packaging/, .github/       systemd unit template, CI workflow
```
