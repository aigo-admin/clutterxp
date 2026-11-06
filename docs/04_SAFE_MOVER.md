# 04: Safe Mover

The riskiest code in the project. It must be small, boring, heavily tested, and free of config/rule/history knowledge. Interface: `Mover.move_file(...)` in `interfaces.py`.

## 1. Guarantees

| # | Guarantee |
|---|---|
| G1 | **Never overwrites** an existing destination path, even under a race with another process. |
| G2 | **Never deletes** user data except the *source*, and only after the destination is fully published and verified. |
| G3 | On any failure the source is left in place and unchanged; partial destination artifacts are removed. |
| G4 | If the source changes during the operation (size/mtime differ from what was observed at start), the move is aborted with `SRC_CHANGED`. |
| G5 | Only regular files are moved. Directories, symlinks (unless explicitly allowed), sockets, devices: `SRC_NOT_REGULAR`. |
| G6 | File content, mode and mtime are preserved. |
| G7 | A crash at any point leaves either (source only), (both, with the destination complete), or (destination only). Never a truncated destination under its final name. |

## 2. Preconditions (checked first, in this order)

1. `lstat(src)`: exists (`SRC_VANISHED`), is a regular file (`SRC_NOT_REGULAR`). If `expect_size`/`expect_mtime_ns` are given, they must match (`SRC_CHANGED`).
2. Destination directory handling per `create` mode:
   - `NEVER`: must exist and be a directory, else `DEST_MISSING`.
   - `LEAF`: `static_root` must exist or its parent must exist (then `mkdir` it). Directories below `static_root` (template-generated) are created. Anything else missing: `DEST_MISSING`.
   - `ALL`: `os.makedirs(exist_ok=True)`.
   Record every directory the mover created in `MoveResult.created_dirs` (deepest last), so undo can remove them if empty.
3. Destination directory is writable (`os.access(W_OK|X_OK)` as a hint; the real test is the syscall result): else `DEST_NOT_WRITABLE`.
4. Destination directory is not the source's own directory (no-op): `DEST_INVALID`.
5. Final name: `new_name or src.name`. Reject names containing `/` or NUL.

## 3. Collision handling

Collision is decided **at publish time**, not only at plan time, because the world changes between preview and apply.

- Policy `SKIP`: if the final path exists (`os.path.lexists`) at plan time, the plan shows `BLOCKED/COLLISION`. At publish time, a `FileExistsError` from the no-clobber primitive produces `MoveResult(ok=False, code=COLLISION)`.
- Policy `RENAME`: candidates are `name (1).ext`, `name (2).ext`, ... (insert before the final suffix; for `.tar.gz` insert before `.tar.gz`). On `FileExistsError` from the primitive, try the next number. Cap at 9999, then `COLLISION`.
- Because the primitive itself refuses to clobber, the retry loop is race-safe.

## 4. Strategy A: same filesystem (`st_dev` equal)

Try in this order; remember per process which ones are unsupported.

1. **`renameat2(src, dst, RENAME_NOREPLACE)`**: one atomic syscall, fails with `EEXIST` if `dst` exists.
   - Python has no wrapper. Use `ctypes.CDLL(None, use_errno=True)`; call `renameat2(AT_FDCWD=-100, src, AT_FDCWD, dst, 1)` with `os.fsencode` paths. Available on glibc 2.28+ and Linux 3.15+; the filesystem must support it. `ENOSYS`, `EINVAL`, `ENOTSUP`: fall through.
2. **`os.link(src, dst, follow_symlinks=False)` then `os.unlink(src)`**: `link` fails with `FileExistsError` if `dst` exists, so it is also no-clobber. Not supported on FAT/exFAT and some network filesystems (`EPERM`, `ENOTSUP`, `EOPNOTSUPP`): fall through.
   - If `unlink(src)` fails after the link succeeded, **remove the new link** (`unlink(dst)`) so the file does not exist twice, then report `PERMISSION`.
3. **Checked rename**: under the process-wide fs lock, `lexists(dst)` then `os.rename`. Only for filesystems that support neither of the above. The race window exists only against *other* programs; document it in the result (`strategy=CHECKED_RENAME`).

All three preserve inode, mtime and mode.

## 5. Strategy B: different filesystems (`st_dev` differs, or any `EXDEV`)

Never delete before verifying. Steps:

1. Record `(size0, mtime_ns0)` from `lstat(src)`.
2. Create a temp file **in the destination directory**: `.<name>.<pid>.<8-hex>.clutterxp-partial`, opened with `O_WRONLY|O_CREAT|O_EXCL`, mode `0o600`.
3. Copy with `shutil.copyfileobj` using a 4 MiB buffer (or `os.copy_file_range`/`sendfile` when available), calling `progress(done, total)` at most about 10 times per second.
4. `os.fsync(fd)`; close.
5. Verify:
   - `lstat(src)` still equals `(size0, mtime_ns0)`, else delete the temp, `SRC_CHANGED`.
   - temp size equals `size0`, else delete the temp, `VERIFY_FAILED`.
   - if `verify_copy: hash`: SHA-256 of source and temp must match.
6. Apply metadata to the temp: `chmod` to the source mode, `os.utime(ns=(atime, mtime_ns0))`, and copy xattrs best-effort (`shutil.copystat` semantics).
7. **Publish** with the no-clobber primitive from section 4 (rename temp to final name; same filesystem as the temp, so `renameat2`/`link` apply). Collision handling per section 3.
8. `fsync` the destination directory.
9. `os.unlink(src)`.
   - If this fails, **keep both files**, return `ok=False, code=PERMISSION`, message says "copied but could not remove source". The history layer marks this specially (`failed`, with `dst_path` set) so `doctor` and undo reasoning handle it.
10. Return `MoveResult(strategy=COPY_VERIFY, bytes_copied=size0)`.

Cleanup rule: any exception between steps 2 and 7 removes the temp file in a `finally`. `ENOSPC` maps to `NO_SPACE`.

## 5a. Crash behavior of Strategy B

| Crash point | State on disk | Recovery |
|---|---|---|
| before publish | src intact, stray `*.clutterxp-partial` | `doctor --fix` removes partials older than 1 h in known destination roots; harmless otherwise |
| after publish, before unlink | src intact AND dst complete | history row stays `pending`; recovery classifies as "both exist" (see `05_HISTORY_UNDO.md`) |
| after unlink | dst only | recovery marks `done` |

## 6. fsync policy

- Cross-filesystem copies: `fsync(file)` and `fsync(dest dir)` always.
- Same-filesystem rename: no explicit fsync (metadata operation; the journal row is the durable record and recovery is reconciliation based).

## 7. Interface notes

- Paths in/out are `pathlib.Path`. Use `os.fsencode`/`os.fsdecode` semantics, so non-UTF-8 byte names work.
- `move_file` never logs to history and never reads config. The caller supplies `collision`, `create`, `static_root`.
- Returns, never raises, for expected failures (wrap `OSError` into `MoveResult`). Unexpected exceptions (bugs) propagate.
- Thread-safe: the only shared state is the supported-primitive cache (a lock-protected dict).

## 8. Test requirements (all mandatory)

- Same-FS happy path for each primitive (force each by monkeypatching the others to raise).
- Collision under both policies, including a **race**: create the destination between the plan and the publish.
- `RENAME` suffixing for names with multiple dots and for `.tar.gz`.
- Cross-FS: use `/dev/shm` as the second filesystem when `st_dev` differs from `tmp_path`; otherwise simulate with `monkeypatch` raising `OSError(errno.EXDEV)` from the same-FS primitives.
- Fault injection: raise at every numbered step in sections 4 and 5; assert G1 through G7 after each.
- Source modified during copy (`SRC_CHANGED`), source vanished, source is a symlink/directory/FIFO, destination unwritable, `ENOSPC` (monkeypatch write).
- Filenames: spaces, unicode, leading `-`, newlines, 255-byte names, invalid UTF-8 bytes.
- Property test: for random file trees, after any successful move the set of file contents is unchanged, only the path differs.
