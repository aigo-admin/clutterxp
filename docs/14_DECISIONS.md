# 14: Decision Log

Format: ID, decision, alternatives, rationale. New decisions are appended; superseded ones are marked, not deleted.

| ID | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-001 | Name: **ClutterXP**; command `clutterxp`, alias `cxp` | `clutter` | Distinct, unlikely to collide with existing packages; short alias for daily use |
| D-002 | Python 3.10+ | Go, Rust | Fast development, Kali ships recent Python, ample libs (watchdog, Typer, Pydantic). Performance-critical paths are syscalls, not Python |
| D-003 | Typer for CLI | argparse, click | Types give help/completion for free; click underneath |
| D-004 | Pydantic v2 for config schema, `extra="forbid"` | dataclasses + manual checks, jsonschema | Clear error paths, JSON Schema export, strictness |
| D-005 | `ruamel.yaml` round trip for config edits | PyYAML | Preserves user comments and ordering across CLI edits |
| D-006 | Lower `priority` number wins; ambiguity only at equal best priority and different destinations | first-listed wins; all ties ambiguous | Predictable and safe; resolves the v0.1 contradiction between priority and ambiguity |
| D-007 | Default `priority` = 100 for all rules | by file order | Overlapping unprioritized rules are reported, never silently ordered |
| D-008 | Token matching with raw and camel streams; digits not split; substring is opt-in | substring default; fuzzy | Prevents `sl` matching `slides`; no fuzzy surprises; typos handled as explicit aliases |
| D-009 | No-clobber move via `renameat2(RENAME_NOREPLACE)`, fallback `link+unlink`, last resort checked rename | `os.rename`, `shutil.move` | `rename` silently overwrites on POSIX; need atomic refusal |
| D-010 | Cross-FS: temp copy in destination dir, fsync, verify, publish no-clobber, then unlink source | `shutil.move` | Never delete before verify; never a partial file under its final name |
| D-011 | Collision default `skip`; `rename` opt-in | ask interactively | Predictable in unattended watch mode |
| D-012 | `create_destinations: leaf` default | `all`, `never` | Catches typos in parent paths while still allowing new project folders and templated subfolders |
| D-013 | Journal-first history (`pending` row committed before the move), `synchronous=FULL` | log after move | Crash recovery requires a record that exists before the change |
| D-014 | Undo verifies size+mtime (hash optional); no-overwrite; per-op independence; default scope = last batch with confirmation | undo single last op | Batch undo is essential after bulk organize; confirmation prevents surprise |
| D-015 | Paths stored with a codec (UTF-8 text, base64 marker for non-UTF-8) | store bytes BLOB | Human-readable DB for the 99.99% case, correct for the rest |
| D-016 | Watcher: quiet period + stable stat + not-open-for-write via `/proc`; `FileClosedEvent` only shortens the wait | fixed sleep; lsof | Cheap, no external binary, handles browser temp+rename and arbitrary moved-in files |
| D-017 | Single worker thread for moves; per-operation fs lock (not lifetime) | thread pool; global lock | Eliminates intra-process races; manual `organize` can run alongside `watch` |
| D-018 | Per-folder watch settings including a rule subset | one global rule set | Makes "any folder, not only Downloads" practical |
| D-019 | Destination templates `{year}` etc. from day one | later | Config format must not change later; cheap to implement now |
| D-020 | Cheap stat-based `conditions` (size, age) in v1.0 | later rule-engine stage | Zero I/O cost beyond the `lstat` already done; high value |
| D-021 | Duplicate pipeline: size, hardlink, partial hash, full SHA-256, SQLite hash cache; separate cache DB | hash everything | Orders of magnitude less I/O; cache never shared with history |
| D-022 | Potential duplicates from a closed list of copy-name patterns only | fuzzy similarity | Predictable and testable; metadata alone never claims "duplicate" |
| D-023 | Root refusal (exit 4) unless `--allow-root`; system-dir denylist | warn only | Kali historically ran as root; hard stop avoids moving system files |
| D-024 | Fs lock per operation + single-watcher lock | lock file existence check | `flock` is released by the kernel on crash; no stale locks |
| D-025 | systemd **user** unit via `service install` | cron, autostart | Clean lifecycle, logs in journal, no root needed |
| D-026 | JSON output with `schema_version` on every command | ad-hoc | Stable contract for scripts and for the GUI test oracle |
| D-027 | Frozen `models.py` and `interfaces.py` before any implementation | emergent interfaces | Lets agents work in parallel without drift; changes go through CONTRACT_CHANGE |
| D-028 | `hatchling` build, `pipx` for end users | setuptools, system pip | Kali enforces PEP 668; pipx isolates the app |
| D-029 | GUI deferred to v1.1; PySide6 as optional extra | Tk, web UI | Qt is capable and licensed LGPL; keeps CLI install light |
| D-030 | No AI, no content reading, no network | optional later | Core promise: fast, local, private, predictable |
| D-031 | Ruff per-file ignores for frozen contracts (`models.py`, `interfaces.py`, `config/schema.py`) | fix the files | These files are locked by `check_contracts.py`; minor style issues (E501 line length, UP035/UP037 import style, SIM102, RUF022, S105 false positive) don't affect correctness and cannot be changed without a CONTRACT_CHANGE. Ignores recorded in `pyproject.toml`. |
| D-032 | `Conditions._ranges` requires `older_than` < `newer_than` duration | `older_than` > `newer_than` | Per docs/02, `older_than: 2d` means age > 2d, `newer_than: 30d` means age < 30d, forming valid range `2d < age < 30d`. Fixed inverted validator. |
| D-033 | Add `ruamel.yaml.clib` dependency to `pyproject.toml` | pure python only | C extension enables 10-15ms parsing for 200 rules on CPython, meeting the 50ms config reload budget. |
| D-034 | `CreateDestinations.LEAF` allows template-generated subdirectories below `static_root` | require all intermediate template parents to pre-exist | Conforms to docs/04 section 2: `static_root` or its parent must exist; directories below `static_root` are created dynamically by the mover. |

