# 01: Architecture

## 1. Layers

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  CLI (Typer) │   │  GUI (Qt)    │   │ systemd unit │   presentation
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       └──────────────────┼──────────────────┘
                   ┌──────▼───────┐
                   │  Organizer   │   facade: plan / apply / process_file / undo / reload_config
                   └──┬──┬──┬──┬──┘
      ┌───────────────┘  │  │  └────────────────┐
┌─────▼─────┐   ┌────────▼┐ ┌▼─────────┐   ┌─────▼──────┐
│ Scanner   │   │  Rule   │ │  Safe    │   │  History   │
│ + Planner │   │ Engine  │ │  Mover   │   │  + Undo    │
└─────┬─────┘   └────┬────┘ └────┬─────┘   └─────┬──────┘
      │         ┌────▼────┐      │               │
      │         │ Config  │      │               │
      │         └─────────┘      │               │
┌─────▼─────────────────────────▼───────────────▼──────┐
│ locking · paths · logging · fs helpers               │   infrastructure
└───────────────────────────────────────────────────────┘
┌──────────────┐  ┌────────────────────┐
│ Watch Service│  │ Duplicate Detector │   standalone services (use facade / models only)
└──────────────┘  └────────────────────┘
```

Rules of the layering:

- `models.py` and `interfaces.py` are imported by everyone and import nothing from the package.
- The **Rule Engine is pure**: input `FileInfo` (already stat-ed), output `Decision`. No I/O.
- The **Mover is a filesystem primitive**: no config, no history, no rules.
- **History** never touches user files; it only records and answers queries.
- Only the **Organizer** wires rule engine + mover + history together. CLI, watcher and GUI never call the mover or rule engine directly (exception: `rules test` calls `RuleEngine.trace`).
- The **Duplicate Detector** is read-only on user files and uses its own cache DB (`cache.db`), so it shares no state with sorting.

## 2. Module map

```
src/clutterxp/
  models.py                 FROZEN contracts
  interfaces.py             FROZEN Protocols
  paths.py                  XDG dirs, env overrides, expanduser/expandvars helpers
  locking.py                flock-based fs lock + single-watcher lock
  logging_setup.py          file + stderr logging, 0600 log file
  config/
    schema.py               Pydantic models (extra="forbid")
    loader.py               load + validate + resolve -> RuntimeConfig
    writer.py               ruamel round-trip edits, atomic write, .bak
    validate.py             cross-rule checks (overlap, loops, denylist)
    jsonschema.py           `config schema` export
  rules/
    tokenizer.py            normalize + tokenize (see docs/02)
    matcher.py              per-keyword matchers, compiled once
    template.py             destination template expansion + sanitizing
    engine.py               RuleEngine implementation (decide, trace)
  scanner.py                directory listing -> FileInfo (no recursion unless asked)
  planner.py                Decision -> PlannedAction (resolves destination, previews collisions)
  mover/
    fsops.py                renameat2 via ctypes, link/unlink, fsync helpers
    safe_move.py            Mover implementation
    collision.py            " (n)" naming
  history/
    db.py                   connection factory, pragmas, migrations
    schema.sql              DDL (see docs/05)
    service.py              History implementation
    undo.py                 undo logic
    recovery.py             pending-op reconciliation
    pathcodec.py            path <-> TEXT codec for non-UTF-8 names
  organizer.py              Organizer implementation
  watch/
    service.py              Observer + worker lifecycle
    events.py               event filtering and mapping
    debounce.py             path -> deadline map (injectable clock)
    settle.py               stability + open-for-write checks
    ignore.py               built-in + user ignore patterns
  duplicates/
    detector.py             staged pipeline
    hashcache.py            SQLite cache keyed by (dev, inode, size, mtime_ns)
    namenorm.py             "potential duplicate" name normalization
  cli/
    app.py                  Typer root, global flags, error -> exit-code mapping
    cmd_*.py                one module per command group
    render.py               Rich tables and JSON emitters
  service_install.py        systemd --user unit generation
  gui/                      (v1.1) PySide6
tests/                      mirrors the package layout
```

## 3. Data flow

### 3.1 `organize` (and `scan`)

```
CLI args ─► Organizer.plan(paths)
              ├─ Scanner: list entries (os.scandir), lstat once per entry -> FileInfo
              ├─ RuleEngine.decide(FileInfo)            (pure, in-memory, precompiled)
              └─ Planner: resolve dest_dir, dest_path, preview collision/permission -> PlannedAction
          ◄─ Plan (nothing on disk changed)
CLI renders plan (table / JSON); --dry-run stops here
bulk? confirm  ─►  Organizer.apply(plan)
              for each WILL_MOVE action (serially, under fs lock):
                 history.begin_op()      -> row status=pending, committed
                 mover.move_file()       -> verified, no-clobber
                 history.finish_op()     -> done | failed
          ◄─ BatchResult
```

`scan` = `plan` + render with `--show`. It never calls `apply`.

### 3.2 `watch`

```
watchdog Observer thread ──► EventFilter ──► Debouncer(path -> deadline) ──► worker thread
                                                                   │
                       settle check (quiet period, stable stat, not open for write)
                                                                   │
                                 Organizer.process_file(path, folder)   ──► one batch, one op
```

One worker thread performs all moves serially. The observer thread only enqueues. Details in `06_WATCHER.md`.

### 3.3 `undo`

`undo` selects operations from history (by op, batch, file or "last batch"), validates each one against the filesystem, moves it back with the same Mover (no-clobber), and marks it `undone`. Details in `05_HISTORY_UNDO.md`.

## 4. Threading and concurrency model

| Component | Threads | Notes |
|---|---|---|
| CLI one-shot commands | 1 (main) | Duplicate scan may use a thread pool for hashing |
| Watch service | observer thread + 1 worker + main (signals) | All DB writes and file moves happen on the worker |
| GUI | Qt main + worker threads for every Organizer call | GUI never blocks on file I/O |

- **SQLite:** one connection per thread (`check_same_thread=True`), WAL mode, `busy_timeout=5000`.
- **Filesystem lock:** `locking.fs_lock()` is an exclusive `flock` on `<state>/fs.lock`, held **per operation** (one move or one undo step), never for the lifetime of the watcher. A manual `organize` can therefore run while `watch` is active; they interleave at file granularity.
- **Single watcher:** `locking.watch_lock()` is a non-blocking `flock` on `<state>/watch.lock` held for the life of the process; a second `watch` fails fast with the PID of the first.
- **Config reload:** the rule engine is immutable once built. Reload builds a new engine, validates, then swaps one reference atomically. An invalid new config is rejected and the old engine keeps running.

## 5. State locations

| What | Default path | Override |
|---|---|---|
| Config | `~/.config/clutterxp/config.yaml` | `--config`, `CLUTTERXP_CONFIG` |
| History DB | `~/.local/state/clutterxp/history.db` | `CLUTTERXP_STATE_DIR` |
| Hash cache DB | `~/.cache/clutterxp/cache.db` | `CLUTTERXP_CACHE_DIR` |
| Logs | `~/.local/state/clutterxp/clutterxp.log` (rotating, 5 x 1 MB) | `CLUTTERXP_STATE_DIR` |
| Locks | `~/.local/state/clutterxp/{fs,watch}.lock` | `CLUTTERXP_STATE_DIR` |

Directories are created `0700`; DB and log files `0600`. `platformdirs` provides the defaults.

## 6. Error handling

- Expected failures derive from `ClutterError` and carry an `ErrorCode`. The CLI maps them to messages and exit codes (`08_CLI.md`).
- One failing file never aborts a batch: it is recorded as `failed`/`blocked`, the batch continues, and the exit code is `3` (partial).
- Unexpected exceptions are logged with traceback to the log file and shown to the user as a short message plus the log path.
- `ConfigError` carries a tuple of problems; the CLI prints each on its own line with the YAML path (e.g. `rules[2].destination: ...`).

## 7. Logging

- Python `logging`, logger root `clutterxp`.
- Levels: `INFO` for moves/undos/watch lifecycle, `WARNING` for skips with a cause, `ERROR` for failures, `DEBUG` for every decision (off by default).
- Human console output is separate from logs; console output goes through `cli/render.py` only.
- Under systemd, stderr goes to the journal; the log file is still written.

## 8. Performance budgets (targets, verified by `perf` tests)

| Operation | Budget |
|---|---|
| Rule decision per file, 100 rules | under 100 µs (precompiled matchers, no I/O) |
| `plan` for 10,000 files, 100 rules (SSD, warm cache) | under 2 s |
| Watch idle CPU | ~0% (no polling), RSS under 60 MB |
| Same-filesystem move | one syscall plus one DB commit |
| Config reload | under 50 ms for 200 rules |

## 9. Extension points (designed now, used later)

- `destination` is a **template string** from day one (placeholders in `02_RULE_ENGINE.md`).
- `conditions` is an open-ended, validated mapping so more conditions can be added without changing the file format.
- JSON output has `schema_version`; the GUI and scripts depend on it.
- The `Organizer` Protocol is the only API the GUI is allowed to use.
