# 05: History, Journal, Undo, Recovery

Storage: SQLite file `history.db` (`0600`).

## 1. Connection settings

```
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = FULL;      -- the pending row must survive power loss; volume is low
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA user_version = <schema version>;   -- migrations run in order, inside one transaction each
```

One connection per thread. Use `BEGIN IMMEDIATE` for writes.

## 2. Schema (version 1)

```sql
CREATE TABLE batches (
  id          INTEGER PRIMARY KEY,
  created_at  TEXT NOT NULL,                 -- ISO-8601 UTC, e.g. 2026-11-06T09:05:00Z
  command     TEXT NOT NULL,                 -- organize | watch | undo | duplicates ...
  folder      TEXT,
  note        TEXT
);

CREATE TABLE operations (
  id            INTEGER PRIMARY KEY,
  batch_id      INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  status        TEXT NOT NULL CHECK (status IN ('pending','done','undone','failed','skipped')),
  src_path      TEXT NOT NULL,               -- see path codec
  dst_path      TEXT,
  rule_name     TEXT,
  strategy      TEXT,                        -- MoveStrategy value
  size          INTEGER,
  mtime_ns      INTEGER,
  inode         INTEGER,
  device        INTEGER,
  sha256        TEXT,                        -- only when settings.record_hash
  created_dirs  TEXT,                        -- JSON array of dirs the move created
  created_at    TEXT NOT NULL,
  finished_at   TEXT,
  undone_at     TEXT,
  error         TEXT
);
CREATE INDEX idx_ops_batch  ON operations(batch_id);
CREATE INDEX idx_ops_status ON operations(status);
CREATE INDEX idx_ops_dst    ON operations(dst_path);
CREATE INDEX idx_ops_rule   ON operations(rule_name);

CREATE TABLE events (
  id        INTEGER PRIMARY KEY,
  ts        TEXT NOT NULL,
  level     TEXT NOT NULL,                   -- info | warning | error
  kind      TEXT NOT NULL,                   -- skip_ambiguous | skip_blocked | error | undo_refused | recovery | watch_start ...
  batch_id  INTEGER,
  op_id     INTEGER,
  path      TEXT,
  message   TEXT NOT NULL
);
CREATE INDEX idx_events_ts ON events(ts);
```

`no_match` skips are **not** recorded unless `settings.history.log_no_match: true`.

## 3. Path codec

Linux file names are bytes. Python gives `str` with surrogate escapes for non-UTF-8 names, which SQLite cannot store. `pathcodec.py`:

- `encode(path) -> str`: if `os.fsencode(path)` decodes as strict UTF-8, store `os.fspath(path)` unchanged. Otherwise store the marker `"\u0001b64:"` followed by the base64 of the raw bytes. A real path can never start with `\u0001` followed by `b64:` in practice, and `decode` checks the marker first.
- `decode(text) -> Path`: if the marker is present, base64-decode and `os.fsdecode`; otherwise `Path(text)`.
- A unit test round-trips random byte names.

## 4. Journal protocol (what makes crashes survivable)

For each file, in this exact order, inside the Organizer:

1. Acquire the fs lock.
2. `history.begin_op(...)` inserts `status='pending'` with src, planned dst-dir, rule, size, mtime_ns, inode, device. **Committed (durable) before any filesystem change.**
3. `mover.move_file(...)`.
4. `history.finish_op(op_id, result)`: one transaction setting `status='done'` plus final `dst_path`, `strategy`, `created_dirs`, `finished_at`; or `status='failed'` plus `error` (and `dst_path` when a copy exists but the source could not be removed).
5. Release the lock.

Skipped/ambiguous/blocked plan entries are written as `events`, not operations.

## 5. Crash recovery (`recovery.py`, run by `doctor --fix` and automatically at `watch`/`organize` start for pending rows older than the current process)

For each `pending` operation, check the filesystem:

| src exists | dst exists | Meaning | Action |
|---|---|---|---|
| yes | no | crashed before the move | mark `failed` ("rolled back: not moved") |
| no | yes, size equals recorded size | crashed after the move | mark `done`, fill `dst_path` |
| yes | yes | crashed between publish and unlink (cross-FS) | if dst size equals src size: **do not delete anything**; mark `failed` with message "both copies exist, remove one manually", log an event, and print the two paths |
| no | no | file moved or removed by someone else | mark `failed` ("source and destination missing") |
| any | unknown dst | `dst_path` not yet recorded | search the planned destination dir for a file with the same name/size/mtime_ns; if exactly one, treat as above, else mark `failed` |

Recovery never moves or deletes user files. It only reconciles records.

## 6. Undo

Selectors (`Organizer.undo`): `op_id`, `batch_id`, or neither (means "last batch with status `done` ops"). The CLI additionally offers `--file PATH` (finds the latest `done` operation whose `dst_path` equals that path).

Operations in a batch are undone **in reverse order of id**. Each is independent (no all-or-nothing): refusals are reported per file and do not block the rest.

### 6.1 Per-operation checks (all must pass, else `UndoOutcome(ok=False, code=...)` and nothing changes)

| # | Check | Failure code |
|---|---|---|
| 1 | status is `done` | `UNDO_NOT_DONE` |
| 2 | `dst_path` exists, is a regular file | `UNDO_DEST_MISSING` |
| 3 | `lstat(dst)` size and mtime_ns equal the recorded values (and SHA-256 if recorded). Waived by `--allow-modified`. | `UNDO_DEST_CHANGED` |
| 4 | original `src_path` does **not** exist | `UNDO_ORIGIN_OCCUPIED`; with `--on-conflict rename` restore as `<stem> (restored)<ext>` instead |
| 5 | original parent directory exists; if missing, recreate it (record in the event log) | `DEST_NOT_WRITABLE` if impossible |

### 6.2 Action

Use `Mover.move_file(dst, original_dir, new_name=original_name, collision=SKIP, create=ALL)`. The same no-clobber and verified-copy guarantees apply, so an undo never overwrites anything. On success: `mark_undone(op_id)`, an `undo` event, and removal of the directories listed in `created_dirs` **only if empty** (deepest first; use `os.rmdir`, never recursive delete).

### 6.3 `--dry-run`

Runs section 6.1 and prints what would happen, changing nothing.

### 6.4 Default scope and confirmation

`clutterxp undo` with no selector shows "Undo last batch #N: M files from 'organize ~/Downloads'" and asks to confirm (skip with `--yes`). This prevents a surprise bulk undo.

## 7. History queries

`list_ops` supports filters: `batch_id`, `status`, `since` (relative like `7d` or ISO), `rule_name`, `path_contains`, `limit`. Output includes batch id so users can `undo --batch`.

## 8. Retention and maintenance

- `history prune --older-than 180d` deletes **rows only** (operations and events older than the cutoff, plus empty batches). Rows with `status='pending'` are never pruned.
- Automatic prune at most once per day when `retention_days > 0`.
- `doctor` runs `PRAGMA integrity_check` and `PRAGMA wal_checkpoint(TRUNCATE)`.
- A corrupt DB is never deleted: it is renamed to `history.db.corrupt-<timestamp>` and a fresh DB is created, with a loud warning.

## 9. Test requirements

- Journal ordering: assert a `pending` row is committed before the mover is called (use a fake mover that reads the DB).
- Crash injection at each protocol step, then run recovery; assert the table in section 5.
- Undo happy path, modified file, missing file, occupied origin (both conflict modes), missing parent, `created_dirs` cleanup (non-empty dir left alone), double undo (second is `UNDO_NOT_DONE`).
- Batch undo with one refusal in the middle: others still restored.
- Path codec round trip for non-UTF-8 names.
- Concurrency: two threads begin/finish ops simultaneously; no lost rows, no `database is locked` errors surfacing.
- Migration test: a v1 DB fixture upgrades cleanly (even if v1 is the only version, test the runner with a fake v2 step).
