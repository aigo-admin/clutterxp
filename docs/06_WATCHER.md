# 06: Watch Service

Automatic sorting for **any configured folders**. Files may arrive by browser download, copy, drag-and-drop, `mv` from elsewhere, extraction from an archive, or sync tools. The watcher must treat all of them correctly and cheaply.

## 1. Pipeline

```
watchdog Observer (inotify) ─► EventFilter ─► Debouncer ─► Worker thread
                                                   ▲            │
                                      re-arm on activity   settle check
                                                                │
                                          Organizer.process_file(path, folder)
```

- The observer thread does **no** filesystem work other than reading the event object; it only enqueues.
- One worker thread owns settle checks, decisions and moves. Serial processing eliminates move races inside the process.

## 2. Event mapping (watchdog 4.x)

| watchdog event | Action |
|---|---|
| `FileCreatedEvent` | candidate: arm/re-arm debounce for `path` |
| `FileModifiedEvent` | if `path` is a candidate: re-arm (file still being written); otherwise ignore (avoids reprocessing files we deliberately left in place) |
| `FileMovedEvent` | forget `src_path`; candidate: `dest_path` if inside a watched folder (this is how browsers publish `foo.part` to `foo.pdf`, and how files moved in from elsewhere arrive) |
| `FileClosedEvent` (inotify `IN_CLOSE_WRITE`) | "writer finished" hint: shortens the quiet period to `min(settle, 1 s)` but all other checks still run |
| `FileDeletedEvent` | cancel the candidate |
| directory events | ignored (directories are never moved); in recursive mode new subdirectories are watched automatically by the observer |
| overflow / observer error | trigger a one-time **reconciliation scan** of that folder (the only case a scan happens automatically) |

Candidates are filtered before arming: ignore patterns (built-in + settings + per-folder), hidden files if configured, clutterxp's own temp files, and paths in the **self-event suppression** set.

## 3. Settle detection (is the file complete?)

A candidate is processed only when **all** hold:

1. **Quiet period**: no event for that path for `settle_seconds` (default 3, per-folder override; `FileClosedEvent` shortens it to at most 1 s).
2. **Stable stat**: two `lstat` samples, 0.5 s apart, have identical `(size, mtime_ns)`. A file that arrives by rename with an old mtime passes immediately after the two samples.
3. **Not open for writing**: on Linux, scan `/proc/*/fd/*` symlinks that resolve to this path, and read `/proc/<pid>/fdinfo/<fd>` `flags:` (octal); if `flags & O_ACCMODE != O_RDONLY` the file is being written. Only readable (same-user) processes can be inspected: unreadable ones are skipped. Cost is paid once per candidate at the end, not per event. If `/proc` is unavailable the check is skipped.
4. **Still a regular file** that still exists and is not a symlink.

If a check fails, re-arm with `settle_seconds` backoff. After `max_wait` (default 6 h) with continuous activity, log a warning and drop the candidate (it can still be processed by a manual `organize`).

## 4. Loop and echo prevention

- **Config validation** forbids destinations inside watched folders (`allow_chain: false`).
- **Self-event suppression**: after a move, add both `src` and `dst` to a `path -> expiry` map (5 s). Events on suppressed paths are dropped.
- If `allow_chain: true`, a file whose last history operation moved it into the current folder less than 60 s ago is not re-processed (guards against A to B to A cycles).

## 5. Behavior rules

- No rule match: nothing happens, nothing is logged at `INFO` (debug only), nothing recorded unless `history.log_no_match`.
- Ambiguous: record one `skip_ambiguous` event per path (de-duplicated until the file changes) and print a warning line once.
- Blocked (collision, missing destination, permission): one warning plus an event; the file stays.
- `initial_scan: true`: at start, run `Organizer.plan` and `apply` for files already present in that folder. Default `false`, so starting the watcher never surprises the user.
- Files being **seeded or managed by another program** (torrent clients, sync tools) can break if moved. Documented caveat: exclude those folders or add ignore patterns.

## 6. Lifecycle and signals

| Signal / event | Behavior |
|---|---|
| `SIGINT`, `SIGTERM` | stop accepting events, finish the in-flight move, flush DB, release locks, exit 0 |
| `SIGHUP` | reload config now |
| config file `mtime_ns` change (checked every 5 s by the worker) | reload config |
| invalid new config | keep the old rules, log `ERROR` with the problems, continue |
| watched folder disappears (unmounted/deleted) | log, retry every 30 s with exponential backoff up to 5 min; resume when it returns |
| inotify limit reached (`ENOSPC` on watch add) | error with the fix: `sysctl fs.inotify.max_user_watches=...`; recursive mode warns when estimating more than 50,000 directories |
| network filesystem (NFS/SMB/FUSE) | warn that inotify is unreliable; `--poll` switches to `PollingObserver` (interval default 10 s) |

Single instance: `locking.watch_lock()`; a second `watch` prints the running PID and exits with code `5`.

## 7. Output

- Default: one human-readable line per event (`12:01:44  moved  invoice_3.pdf  ->  ~/Documents/Invoices/2026  [Invoices]`).
- `--json`: one JSON object per line (JSON Lines) with `schema_version`.
- `--quiet`: errors only. `--dry-run`: decide and log what would be moved, move nothing (great for trying a new rule set).
- Under systemd, logs go to stderr/journal without color.

## 7a. Service installation

`clutterxp service install` writes `~/.config/systemd/user/clutterxp.service` with the absolute path to the running executable (`shutil.which("clutterxp")`), runs `systemctl --user daemon-reload` and `enable --now`. `uninstall`, `status`, and `logs` (`journalctl --user -u clutterxp`) complete the set. Template in `packaging/clutterxp.service`.

## 8. Resource use

- Idle: no timers except the 5 s config-mtime check; no directory scans.
- Memory: the debounce map holds only in-flight candidates; bounded to 10,000 entries (overflow triggers a reconciliation scan instead of unbounded growth).
- Bursts (unzipping 5,000 files): events coalesce per path; the worker processes in deadline order.

## 9. Test requirements

- Inject a fake clock into `Debouncer` and `Settler`; unit-test timing logic without sleeping.
- Integration (`slow` marker, short settle values): create-then-write-slowly file, `.part` to final rename, file moved in with old mtime, file opened for write by a helper subprocess (must wait), burst of 500 files, deletion before settle, config reload (valid and invalid), SIGHUP, SIGTERM during an in-flight move, second instance refused.
- Assertions: each file processed exactly once; unmatched files untouched; no events processed for clutterxp's own moves; history has one op per moved file.
