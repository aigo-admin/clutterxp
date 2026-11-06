# 15: Acceptance Criteria

A milestone is done only when **every** criterion below it is demonstrably true and covered by an automated test (named in the test file as `test_A<NN>_...`). The Orchestrator runs `pytest -k "test_A"` as the milestone gate.

## M1: Core (A1 to A25)

| ID | Criterion |
|---|---|
| A1 | User defines rules (name, keywords/aliases, destination) in YAML; nothing is built in |
| A2 | `~` and `$VARS` expand in destinations; unresolved variable is a config error |
| A3 | Unknown config keys are errors with a did-you-mean hint |
| A4 | `config validate` reports all problems in one run, each with its YAML path |
| A5 | A rule with no positive criterion is rejected |
| A6 | Every row of the tokenizer/matching test-vector table (`02`, section 5) passes |
| A7 | Alias `sl` matches `SL-CTF.pdf` but never `slides.pdf` |
| A8 | `glob:` and `regex:` keywords work on full file names; invalid regex is a config error |
| A9 | Extension filters work, case-insensitive, including compound `tar.gz` |
| A10 | `min_size`, `max_size`, `older_than`, `newer_than` conditions work from `lstat` data only |
| A11 | `all_of` and `exclude` behave as specified |
| A12 | Lower priority number wins over higher |
| A13 | Equal best priority with different destinations is `AMBIGUOUS`; same destination is not |
| A14 | `rules test` explains every rule for a given name, with `--json` |
| A15 | Destination templates expand correctly; `..`/separators in values are rejected |
| A16 | `scan` classifies files as match/unmatched/ambiguous/blocked and writes nothing |
| A17 | `organize --dry-run` leaves the tree byte-identical (snapshot test) |
| A18 | `organize` moves only files whose decision is `MATCH` |
| A19 | Unmatched files remain in place and no "Unsorted" folder is ever created |
| A20 | Ambiguous files are never moved and are listed with their candidates |
| A21 | Collisions never overwrite: `skip` reports `BLOCKED`, `rename` produces `name (1).ext` |
| A22 | The no-clobber guarantee holds under a race (destination appears after planning) |
| A23 | Cross-filesystem moves use copy, fsync, verify, publish, then unlink the source, and abort with source intact on any failure |
| A24 | `create_destinations` `never`/`leaf`/`all` behave as specified; missing destination is reported, never silently redirected |
| A25 | `organize` works on any path, with or without a config `watch` entry; denylisted system directories and root execution are refused |

## M2: History and undo (A26 to A40)

| ID | Criterion |
|---|---|
| A26 | Every successful move has a batch and an operation row with src, dst, rule, strategy, size, mtime |
| A27 | The `pending` row is durable before the first filesystem change (proved by a fake mover reading the DB) |
| A28 | `history` filters by batch, status, since, rule and path text |
| A29 | `history show ID` prints the operation and related events |
| A30 | `undo` with no selector undoes the last batch after showing it and confirming |
| A31 | `undo --op`, `--batch` and `--file` select correctly |
| A32 | Undo refuses when the moved file was modified (size/mtime/hash); `--allow-modified` waives only that check |
| A33 | Undo refuses when the original path is occupied; `--on-conflict rename` restores as `(restored)` |
| A34 | Undo never overwrites any file |
| A35 | Undo recreates a missing parent, and removes only empty directories that the original move created |
| A36 | A refusal in a batch undo does not block the others; exit code is `3` |
| A37 | Undoing an already undone operation is refused |
| A38 | Crash recovery handles every case in the recovery table and never moves or deletes user files |
| A39 | Non-UTF-8 file names round-trip through history and undo |
| A40 | `history prune` removes rows only; a corrupt DB is renamed, never deleted, and a fresh DB is created |

## M3: Watch (A41 to A52)

| ID | Criterion |
|---|---|
| A41 | A file appearing in any configured folder is sorted once it has settled |
| A42 | Browser-style `.part` then rename is handled without moving the partial file |
| A43 | A file moved into a watched folder (old mtime) is processed |
| A44 | A file held open for writing by another process is not moved until closed |
| A45 | Built-in and custom ignore patterns are honored |
| A46 | Unmatched files are untouched by the watcher |
| A47 | A burst of 500 files is processed exactly once each |
| A48 | The watcher ignores events caused by its own moves; destinations inside watched folders are rejected at config time |
| A49 | Per-folder `recursive`, `ignore`, `settle_seconds` and `rules` subset are honored |
| A50 | Valid config changes reload live (mtime or SIGHUP); invalid ones are rejected and old rules continue |
| A51 | SIGTERM stops gracefully after the in-flight move; a second watcher is refused with exit `5` |
| A52 | Missing/unmounted folder is retried with backoff; inotify limit gives an actionable error; `--poll` works |

## M4: Duplicates (A53 to A60)

| ID | Criterion |
|---|---|
| A53 | Byte-identical files are reported as exact groups with the same SHA-256 |
| A54 | Same size with different content is not reported as exact |
| A55 | Equal head/tail/size but different middle is separated by the full hash |
| A56 | Hardlinks are reported as `hardlink` and excluded from wasted-space totals |
| A57 | Potential duplicates come only from the closed name-pattern list and are labeled review-only |
| A58 | A repeat scan performs no full re-hash; a changed file invalidates its cache entry |
| A59 | The scan never writes, moves or deletes user files and never runs as part of sorting |
| A60 | Table, JSON and CSV outputs work; Ctrl-C stops cleanly with cache preserved |

## M5: Product (A61 to A70)

| ID | Criterion |
|---|---|
| A61 | Every command supports `--json` with `schema_version`, validated against schemas in tests |
| A62 | Exit codes match `08_CLI.md` |
| A63 | `init` writes a commented config with `0600` permissions and refuses to overwrite without `--force` |
| A64 | `rules add/edit/remove/enable/disable` preserve comments, validate before writing, keep a `.bak` |
| A65 | `doctor` runs all listed checks; `--fix` reconciles pending operations |
| A66 | `service install/uninstall/status/logs` manage the systemd user unit |
| A67 | Shell completion installs and works |
| A68 | The wheel installs with `pipx` in a clean environment; `clutterxp` and `cxp` both run |
| A69 | Bulk apply asks for confirmation above `confirm_threshold`; without a TTY and without `--yes` it exits `4` |
| A70 | The README quickstart is verified by an automated smoke script |

## M6: Hardening, v1.0 (A71 to A80)

| ID | Criterion |
|---|---|
| A71 | Line coverage is at least 90% (excluding `gui/`) |
| A72 | Tests for invariants S1 to S15 exist and pass |
| A73 | The fault-injection sweep over mover and journal passes |
| A74 | The security review checklist (`09`, section 5) is complete and all findings are fixed |
| A75 | Performance budgets (`01`, section 8) are met |
| A76 | Grep-enforced rules pass (no stray `shutil.move`/`os.rename`/`rmtree`, no network modules, no `shell=True`) |
| A77 | CI is green on Python 3.10, 3.11, 3.12 and 3.13 |
| A78 | `pip-audit` reports no known vulnerabilities |
| A79 | An end-to-end scenario script (init, rules, organize, undo, watch, duplicates) passes in a clean container |
| A80 | `v1.0.0` is tagged with a complete `CHANGELOG.md` |

## M7: GUI, v1.1 (A81 to A90)

| ID | Criterion |
|---|---|
| A81 | The GUI launches via `clutterxp gui`; the CLI never imports Qt |
| A82 | Dashboard shows watcher state and recent operations |
| A83 | Folders view edits watch folders and per-folder settings |
| A84 | Rules editor includes the live "test filename" trace |
| A85 | Preview data equals the CLI `--json` plan for the same fixture |
| A86 | History view undoes batches/files and shows exact refusal reasons |
| A87 | Duplicates view runs with progress and cancel |
| A88 | `duplicates quarantine` previews, confirms, moves (never deletes), is undoable, exact groups only |
| A89 | The UI thread never blocks on file I/O (worker threads; offscreen tests) |
| A90 | The `gui` extra installs cleanly and is documented |

## Mapping from the original v0.1 MVP criteria

| v0.1 criterion | Now covered by |
|---|---|
| 1. user configures categories and keywords | A1, A3, A4 |
| 2. user configures destination paths | A2, A15, A24 |
| 3. manual scan identifies matches | A16 |
| 4. dry-run changes nothing | A17 |
| 5. organize moves only valid matches | A18 |
| 6. no keyword match stays put | A19 |
| 7. ambiguous matches not guessed | A13, A20 |
| 8. collisions never overwrite | A21, A22 |
| 9. every successful move recorded | A26, A27 |
| 10. safe undo | A30 to A37 |
| 11. sorting needs no hashing/reading | S7 (A72) |
| 12. CLI works without GUI | A81 |
| 13. tests cover matching, no-match, collisions, moves, undo | A6 to A22, A30 to A37, A71 |
