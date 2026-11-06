# 09: Safety

The product is trusted only if it cannot lose or clobber files. This document is the contract for every agent and reviewer.

## 1. Invariants (each must have at least one automated test)

| ID | Invariant |
|---|---|
| S1 | A file is moved only when a rule **explicitly** matches and the decision is `MATCH`. |
| S2 | `NO_MATCH`, `AMBIGUOUS` and `IGNORED` files are never touched. |
| S3 | No operation overwrites an existing path (move, undo, copy fallback, `RENAME` collision loop). |
| S4 | No user file is deleted. The only `unlink` of user data is the source after a verified publish; undo uses the same rule. Directories are removed only if empty, and only those ClutterXP created. |
| S5 | A pending journal row is durable **before** the first filesystem change. |
| S6 | Preview, scan, dry-run and `duplicates scan` perform **zero** writes to user folders (assert by snapshotting the tree before and after). |
| S7 | Sorting never reads file contents and never hashes (except opt-in `verify_copy: hash` / `record_hash`). |
| S8 | Only regular files are moved. Symlinks are not followed unless `follow_symlinks: true`, and never in destination path components created by ClutterXP. |
| S9 | A destination inside a watched folder is rejected unless `allow_chain: true`. |
| S10 | Root refusal: if `geteuid() == 0` the program exits `4` unless `--allow-root`. |
| S11 | System directories are never used as source roots or destinations (denylist below). |
| S12 | All state files are created with `0600` (dirs `0700`). No network access anywhere in the package. |
| S13 | Template-expanded destinations cannot escape their static root (no `..`, no separators in substituted values). |
| S14 | Two processes cannot move/undo the same file concurrently (fs lock), and two watchers cannot run (watch lock). |
| S15 | A failing file never aborts the batch or corrupts others. |

## 2. Denylist (source roots and destinations)

Exact paths and anything beneath them: `/`, `/bin`, `/boot`, `/dev`, `/etc`, `/lib`, `/lib32`, `/lib64`, `/proc`, `/root` (unless running as root with `--allow-root`), `/run`, `/sbin`, `/sys`, `/usr`, `/var/lib`, `/var/log`.

Additional rules:
- The user's **home directory itself** is allowed as a non-recursive source (people do organize `~`), but a **recursive** watch or scan of `$HOME` is refused with a clear message (it would touch dotfiles, application data and every project).
- `~/.ssh`, `~/.gnupg`, `~/.config`, `~/.local/share` and any path component starting with `.` are never **destinations**, and are skipped as recursive descendants (`skip_hidden`).
- Paths are compared after `os.path.realpath` of the **existing** prefix, so symlinks cannot be used to sneak into the denylist.

## 3. Threat model (local tool, single user)

| Threat | Mitigation |
|---|---|
| Rule typo moves many files to a wrong place | preview, confirmation threshold, batch undo, ambiguity detection, short-token safety |
| Crash/power loss mid-move | journal-first, no-clobber publish, reconciliation, never truncated destination under final name |
| Another program writes or reads a file while moved | settle detection, open-for-write check, `SRC_CHANGED` re-verification |
| Malicious file names (`../`, newlines, `-rf`, huge names, invalid UTF-8) | `Path` objects only, no shell, name validation, path codec, destination containment |
| Symlink tricks (race a symlink into a destination dir) | `link(..., follow_symlinks=False)`, `lstat`, `O_EXCL`, destination realpath checks |
| TOCTOU between check and move | primitives that fail atomically (`RENAME_NOREPLACE`, `link`, `O_EXCL`); preconditions re-checked at apply time |
| Tampered config (hostile rules) | config is user-owned `0600`; destinations still pass denylist and containment |
| Regex denial of service | static check for nested quantifiers, regex only on file names (max 255 chars) |
| Information disclosure | DB/log `0600`; no telemetry; no network imports (a test greps the package for `socket`, `urllib`, `requests`, `http`) |
| Privilege misuse | root refusal; the systemd unit is a **user** unit with `NoNewPrivileges=yes` |

## 4. No shell, ever

No `subprocess` with `shell=True`. `subprocess` is used only for `systemctl --user`, `journalctl --user`, and `$EDITOR`, always with an argument list. Ruff rules `S602/S603/S605` are enforced.

## 5. Security review checklist (used by the security-review agent)

- [ ] Every filesystem-mutating call site is in `mover/`, `history/undo.py` (via the mover), or `config/writer.py` (temp+replace) or `service_install.py`.
- [ ] No `shutil.move`, `os.replace`, `os.rename` on user files outside `mover/fsops.py` (grep-enforced by a test).
- [ ] No `os.remove`/`unlink`/`rmdir`/`shutil.rmtree` on user paths outside the documented places (source-after-verified-publish, empty created dirs, own temp files).
- [ ] Every public function that takes a path from config or CLI is routed through denylist and containment checks.
- [ ] Fault-injection suite exists and passes for the mover and the journal.
- [ ] Fuzz/property tests for tokenizer, template expansion, path codec.
- [ ] File permission assertions on DB, log, config, lock files.
- [ ] Dependency audit (`pip-audit`) clean; dependencies pinned with lower bounds only in `pyproject.toml`, locked in CI.
