# 10: Testing Strategy

Tooling: `pytest`, `pytest-cov`, `pytest-timeout`, `hypothesis`, `ruff`, `mypy --strict`.

## 1. Layout

```
tests/
  conftest.py              shared fixtures (see section 2)
  helpers.py               tree builders, snapshot/compare, fake clock, fake mover
  rules/                   tokenizer, matcher, engine, template, test vectors
  config/                  schema, validation, writer (comment preservation), reload
  mover/                   same-fs, cross-fs, collisions, fault injection
  history/                 journal, undo, recovery, pathcodec, concurrency, migrations
  organizer/               plan/apply integration with real temp dirs
  watch/                   debounce/settle units + slow integration
  duplicates/              pipeline, cache, namenorm
  cli/                     CliRunner tests per command, JSON output
  safety/                  invariants S1..S15, grep-enforced rules, permissions
  perf/                    budgets from docs/01 section 8
```

## 2. Fixtures and helpers

- `env` : sets `CLUTTERXP_CONFIG`, `CLUTTERXP_STATE_DIR`, `CLUTTERXP_CACHE_DIR` into `tmp_path`, so tests never touch the real home.
- `make_tree(spec)` : build files from a dict (`{"inbox/a.pdf": b"..", "inbox/.hidden": b""}`) and return paths.
- `snapshot(path)` / `assert_unchanged(before, after)` : recursive `(relpath, size, mtime_ns, sha256)` listing for S6 assertions.
- `second_fs` : a directory on a different `st_dev` than `tmp_path` (try `/dev/shm`, then `/tmp`); `pytest.skip` if none, but the EXDEV simulation test always runs.
- `fake_clock` : injectable monotonic clock for `Debouncer`/`Settler`.
- `FakeMover`, `FakeHistory`, `FakeRuleEngine` implementing the Protocols, for parallel development.
- `config_text(...)` : builds YAML configs programmatically.

## 3. Test pyramid and targets

| Level | What | Coverage target |
|---|---|---|
| Unit | pure logic: tokenizer, matchers, templates, schema, path codec, name normalization, debounce math | 95% of `rules/`, `config/`, `history/pathcodec.py`, `duplicates/namenorm.py` |
| Component | mover, history, undo, recovery with real temp dirs | 95% of `mover/`, `history/` |
| Integration | Organizer plan/apply/undo end to end, CLI via `CliRunner`, watcher with real watchdog | 85% overall |
| Property | hypothesis on tokenizer, templates, collision names, path codec, move-preserves-content | n/a |
| Fault injection | `crash` marker: raise at every step of mover and journal, then run recovery | all steps in `04` and `05` |
| Safety | S1..S15 each have a named test: `test_S3_never_overwrites_*` | 100% of invariants |
| Perf | `perf` marker: budgets from `01_ARCHITECTURE.md` | budgets met on CI |

Overall line coverage gate: **90%** on `src/clutterxp` excluding `gui/`.

## 4. Fault-injection pattern

Wrap the mover's primitives and the history's `finish_op` with a `Fault(step_name)` helper that raises `KeyboardInterrupt`-like `SimulatedCrash` at the n-th call. For each step:

1. Run the operation, expect `SimulatedCrash`.
2. Assert the on-disk states allowed by G7 and the pending row exists.
3. Run `recovery.reconcile()`.
4. Assert the recovery table in `05_HISTORY_UNDO.md` section 5 and that no user file content was lost (compare against the snapshot).

## 5. Watcher tests

- Unit: `Debouncer`, `Settler` with the fake clock. No sleeping.
- Integration (`@pytest.mark.slow`): real `Observer` on `tmp_path`, `settle_seconds=0.3`, polling assertions through a helper `wait_until(predicate, timeout=5)`. Never use bare `time.sleep` for assertions.
- A helper subprocess keeps a file open for writing to test the open-for-write check.

## 6. CLI tests

`CliRunner` with the `env` fixture. For every command: success path, `--json` output validated against a JSON Schema in `tests/cli/schemas/`, error path with exact exit code, non-TTY behavior (confirmations exit `4` without `--yes`).

## 7. CI (GitHub Actions, `.github/workflows/ci.yml`)

Jobs:
1. `lint`: `ruff check`, `ruff format --check`, `mypy`.
2. `test`: matrix Python 3.10, 3.11, 3.12, 3.13 on `ubuntu-latest`; `pytest -m "not perf"` with coverage gate.
3. `crossfs`: `pytest -m crossfs` using `/dev/shm`.
4. `perf`: `pytest -m perf` (non-blocking in PRs, tracked).
5. `audit`: `pip-audit`.

## 8. Definition of done for any module

- Public functions typed, `mypy --strict` clean.
- Unit and component tests included, meeting its coverage target.
- Fault-injection tests for anything that mutates state.
- Docstrings on public API; no TODO left without an issue reference in `docs/13_ROADMAP.md`.
- No new dependency without an entry in `docs/14_DECISIONS.md`.
