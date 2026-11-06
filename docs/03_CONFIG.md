# 03: Configuration

Format: YAML, validated by Pydantic v2 (`extra="forbid"`: unknown keys are errors, with a did-you-mean hint). Parsing and writing use `ruamel.yaml` round-trip mode so user comments and ordering survive `rules add/edit/remove`.

## 1. File location

`~/.config/clutterxp/config.yaml` (override: `--config PATH` or `CLUTTERXP_CONFIG`). `clutterxp init` creates it, `0600`, with a commented example. A missing config is not a crash: commands that need rules print "no config, run `clutterxp init`" and exit `2`.

## 2. Full schema

```yaml
# yaml-language-server: $schema=./clutterxp.schema.json
version: 1                       # required; migrations key off this

settings:
  collision: skip                # skip | rename             (default skip)
  create_destinations: leaf      # never | leaf | all        (default leaf)
  match: token                   # default match mode for plain keywords
  case_sensitive: false
  skip_hidden: true              # ignore dotfiles
  follow_symlinks: false
  confirm_threshold: 10          # organize asks to confirm when moving more than N files (0 = always ask)
  settle_seconds: 3              # watcher quiet period default
  verify_copy: size              # size | hash  (cross-filesystem copies only)
  record_hash: false             # store SHA-256 in history at move time (costs a read of the file)
  history:
    retention_days: 365          # 0 = keep forever; prune removes DB rows only, never files
    log_no_match: false          # record no-match skips in history events (noisy in watch mode)
  ignore: []                     # extra glob patterns ignored everywhere (added to built-ins)
  allow_chain: false             # allow a destination inside a watched folder

watch:                           # folders the watcher monitors and `organize` (no args) processes
  - path: ~/Downloads
    recursive: false
    settle_seconds: 3            # overrides settings.settle_seconds
    ignore: ["*.iso.part"]       # extra patterns for this folder only
    rules: all                   # all | [RuleName, ...]  (subset applied to this folder)
    initial_scan: false          # when `watch` starts, also organize files already present

rules:
  - name: ...                    # see docs/02_RULE_ENGINE.md
```

## 3. Built-in ignore patterns (always applied; cannot be disabled except by `--no-builtin-ignore` in debugging)

```
*.crdownload  *.part  *.partial  *.tmp  *.temp  *.download  *.opdownload
*.!qB  *.aria2  *.swp  *.swx  *~  ~$*  .~lock.*#  *.clutterxp-partial
```

Hidden files (`.*`) are additionally ignored when `skip_hidden: true`.

## 4. Validation (done at load, reported all at once)

Per field:
- `version` must be a supported integer; newer than supported yields an "upgrade ClutterXP" error.
- `name`: non-empty, unique case-insensitively, no path separators.
- `priority`: integer 0 to 100000.
- `destination`: non-empty; `~` and env vars expanded; unresolved `$VAR` is an error; must be absolute after expansion; unknown template placeholders are errors.
- `extensions`: normalized (strip leading dot, lowercase); must not contain `/`.
- `keywords` / `all_of` / `exclude`: non-empty strings; `regex:` compiled and statically checked; `glob:` validated.
- `conditions`: parsed sizes/durations; `min_size <= max_size`; `older_than`/`newer_than` not contradictory.
- A rule needs at least one positive criterion.

Cross-config (`validate.py`):
- **Error**: destination equals or is inside a watched folder, unless `allow_chain: true`. This is the infinite-loop guard. If a recursive watch folder contains a destination, the same applies.
- **Error**: a watch folder or destination resolves into the system denylist (`09_SAFETY.md`).
- **Error**: watch folder does not exist (for `watch`/`organize`; only a warning for `config validate`).
- **Error**: a per-folder `rules:` list names an unknown rule.
- **Warning**: two enabled rules share a keyword and the same priority and different destinations (will be ambiguous).
- **Warning**: `substring` keyword shorter than 4 characters.
- **Warning**: destination static root does not exist (and `create_destinations: never`).
- **Warning**: destination on a different filesystem than a watched folder (moves will copy).

`clutterxp config validate [--strict]` prints errors and warnings; `--strict` makes warnings fail (exit `2`).

## 5. Runtime representation

`loader.load()` returns an immutable `RuntimeConfig` containing resolved absolute paths, compiled rules (handed to the rule engine), watch folder settings, and settings. It also records the config file's `mtime_ns` for hot-reload detection.

## 6. Editing from the CLI

`rules add|edit|remove|enable|disable` go through `writer.py`:

1. Load round-trip document.
2. Apply the change in memory.
3. Validate the **resulting** document with the full schema and cross-checks. Invalid result: abort, file untouched.
4. Copy the current file to `config.yaml.bak`.
5. Write to a temp file in the same directory, `fsync`, `os.replace`.

Comments and key order are preserved. Whole-file rewrites never happen without validation.

## 7. JSON Schema

`clutterxp config schema > clutterxp.schema.json` emits `model_json_schema()` of the root model. The `# yaml-language-server` header gives users autocomplete and inline validation in VS Code and other editors.

## 8. Environment variables

| Variable | Effect |
|---|---|
| `CLUTTERXP_CONFIG` | config file path |
| `CLUTTERXP_STATE_DIR` | history DB, logs, locks |
| `CLUTTERXP_CACHE_DIR` | hash cache |
| `NO_COLOR` | disable colors |

## 9. Versioning and migration

`version: 1` is the first schema. For future versions, `loader.py` holds an ordered list of pure `migrate_N_to_N+1(dict) -> dict` functions; `clutterxp config migrate` applies them, keeps a `.bak`, and shows a diff. v1 ships with the registry in place and zero migrations.
