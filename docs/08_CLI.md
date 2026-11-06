# 08: CLI Reference

Binary: `clutterxp` (alias `cxp`). Built with Typer; output with Rich. Every command works without a GUI and without a TTY.

## 1. Global flags

| Flag | Meaning |
|---|---|
| `--config PATH` | config file (env `CLUTTERXP_CONFIG`) |
| `--json` | machine-readable output (`schema_version: 1`); no colors, no prompts |
| `-q, --quiet` | errors and results only |
| `-v, --verbose` | more detail; `-vv` enables DEBUG logging to stderr |
| `--no-color` | disable colors (also honors `NO_COLOR`) |
| `--allow-root` | permit running as root (otherwise refused, see `09_SAFETY.md`) |
| `--version`, `--help` | |

Shell completion: Typer's `--install-completion` / `--show-completion`.

## 2. Exit codes

| Code | Meaning |
|---|---|
| 0 | success (including "nothing to do" and files left unsorted/ambiguous) |
| 1 | unexpected error (details in the log file) |
| 2 | usage or configuration error |
| 3 | partial failure: some files failed, were blocked, or were refused (undo) |
| 4 | refused by a safety rule (root, system directory, confirmation required in a non-interactive session) |
| 5 | lock held by another instance |

`--strict` on `scan`/`organize` additionally returns `3` if any file was ambiguous.

## 3. Commands

### `init`
`clutterxp init [--minimal] [--force]`
Creates the config directory and a commented `config.yaml` (from `config.example.yaml`; `--minimal` writes only `version` and a `watch` entry for `~/Downloads`). Refuses to overwrite without `--force`. Interactive prompts are skipped in `--json` mode.

### `config`
- `config path` prints the resolved path.
- `config show` prints the effective settings (paths expanded).
- `config validate [--strict]` runs full validation, prints errors/warnings with YAML paths.
- `config schema` prints the JSON Schema.
- `config edit` opens `$EDITOR` on a temp copy, validates on exit, replaces the file atomically only if valid.
- `config migrate` (future-proof; v1 has nothing to do).

### `rules`
- `rules list [--all]` table: name, priority, enabled, keywords, extensions, destination.
- `rules show NAME`.
- `rules add [--name N] [--keyword K ...] [--all-of K ...] [--exclude K ...] [--ext E ...] [--dest PATH] [--priority N] [--collision skip|rename] [--match token|prefix|substring]`: any missing required value is prompted for (not in `--json` mode).
- `rules edit NAME [same options]`, `rules remove NAME [--yes]`, `rules enable NAME`, `rules disable NAME`.
- `rules test FILENAME [--file PATH] [--folder PATH]`: explains every rule's result (`02_RULE_ENGINE.md` section 8). Needs no file to exist.
- `rules check`: alias for validating rule overlaps (equal-priority shared keywords).

### `scan`
`clutterxp scan [PATH ...] [--recursive] [--show all|matched|unmatched|ambiguous|blocked]`
Builds a plan and reports. Never modifies anything. With no PATH, uses every `watch` folder from config.

### `organize`
`clutterxp organize [PATH ...] [-n/--dry-run] [-y/--yes] [--recursive] [--rule NAME ...] [--limit N] [--strict]`
1. Builds the plan and prints it: moves (with rule and destination), no-match (left untouched), ambiguous, blocked.
2. `--dry-run` stops here.
3. If the number of moves exceeds `settings.confirm_threshold`, asks to confirm unless `--yes`. Without a TTY and without `--yes`, exits `4`.
4. Applies with a progress bar, prints a summary and the **batch id** (`Undo with: clutterxp undo --batch 42`).
`--rule` limits the run to named rules; `--limit` caps the number of moves.

### `watch`
`clutterxp watch [PATH ...] [--poll] [--dry-run] [--once]`
Runs the watch service (`06_WATCHER.md`). With no PATH, uses config `watch` entries. `--once` performs one `initial_scan` style pass and exits. Ctrl-C stops cleanly.

### `history`
- `history [--limit 50] [--batch ID] [--status done|undone|failed|pending] [--since 7d] [--rule NAME] [--path TEXT]`
- `history show ID`: one operation with all fields and related events.
- `history prune --older-than 180d [--yes]`: deletes rows only.

### `undo`
`clutterxp undo [--op ID | --batch ID | --file PATH] [-n/--dry-run] [-y/--yes] [--on-conflict skip|rename] [--allow-modified]`
Rules in `05_HISTORY_UNDO.md` section 6. No selector means the last batch (confirmation shown).

### `duplicates`
`clutterxp duplicates scan PATH ... [options]` (`07_DUPLICATES.md`). `duplicates cache --clear`.

### `service`
`service install|uninstall|status|logs`: systemd user unit for `watch`.

### `doctor`
`clutterxp doctor [--fix]`: checks config validity, DB integrity, pending operations (recovery with `--fix`), stale partial files, destination reachability and permissions, filesystem boundaries (copy vs rename), inotify limits, running-as-root, lock state, log/DB permissions. Prints a checklist; exits `3` if any check fails.

### `gui` (v1.1)
Launches the GUI if installed with the `gui` extra; otherwise prints the install hint.

## 4. Output design

Human output, e.g. `organize --dry-run`:

```
Plan for ~/Downloads  (214 files)

 MOVE   invoice_march.pdf          ->  ~/Documents/Invoices/2026        [Invoices  keyword 'invoice']
 MOVE   SL-CTF-writeup.pdf         ->  ~/Projects/SecLeaf               [SecLeaf   keyword 'sl-ctf']
 AMBIG  tc_sl_notes.txt            candidates: SecLeaf, ThunderCipher (both priority 10)
 BLOCK  report.pdf                 destination already has report.pdf (collision: skip)
 KEEP   photo.jpg                  no rule matched; left untouched
 ...
Summary: 38 will move, 170 unmatched (untouched), 3 ambiguous, 3 blocked
Nothing was changed (dry run).
```

`KEEP` lines are collapsed by default in large plans (`--show all` expands them). Colors are supplementary only; each status word carries the meaning.

JSON (`--json`) for `organize`:

```json
{
  "schema_version": 1,
  "command": "organize",
  "dry_run": false,
  "batch_id": 42,
  "summary": {"moved": 38, "unmatched": 170, "ambiguous": 3, "blocked": 3, "failed": 0},
  "actions": [
    {"status": "will_move", "src": "...", "dst": "...", "rule": "Invoices", "matched_by": "keyword 'invoice' (token)"},
    {"status": "ambiguous", "src": "...", "candidates": ["SecLeaf", "ThunderCipher"]},
    {"status": "no_match", "src": "..."},
    {"status": "blocked", "src": "...", "code": "collision", "message": "..."}
  ]
}
```

## 5. Error messages

Short, specific, actionable, and never a traceback by default:

```
error: rules[1].destination: unknown placeholder {yeer} (did you mean {year}?)
error: destination ~/Downloads/Sorted is inside watched folder ~/Downloads (would loop). Move it or set settings.allow_chain.
error: refusing to run as root. Run as your normal user, or pass --allow-root if you really mean it.
```

## 6. Implementation notes for the agent

- One module per command group in `cli/`; all call only the `Organizer` facade (plus `RuleEngine.trace` for `rules test`, `History` for history queries).
- A single `handle_errors` wrapper converts `ClutterError` subclasses to messages and exit codes.
- `render.py` owns all printing; commands pass it data objects, so `--json` and tables stay in sync.
- Tests use `typer.testing.CliRunner` with temp config/state dirs via env vars, and assert both exit codes and JSON output.
