# 16: Risk Register

Likelihood and impact: L/M/H. Owner is the agent or role responsible for the mitigation.

| ID | Risk | L | I | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Data loss in the mover (bug, crash, race) | M | H | no-clobber primitives, verified copy, journal-first, fault-injection suite, security review | A3, A11, A12 |
| R2 | Short or ambiguous keywords move files to the wrong place | H | M | token matching, ambiguity detection, `rules test`, preview, batch undo, config overlap warnings | A2 |
| R3 | Watcher moves a file still being written | M | H | quiet period + stable stat + open-for-write check, `SRC_CHANGED` check at move time | A8 |
| R4 | Move/undo loops from destinations inside watched folders | M | M | config validation, self-event suppression, chain guard | A1, A8 |
| R5 | Torrent/sync clients break when files are moved | M | M | documented caveat, ignore patterns, per-folder rule subsets | A8, docs |
| R6 | `renameat2` unavailable (musl, old kernels, some filesystems) | M | L | ordered fallbacks, per-process capability cache, tests force each path | A3 |
| R7 | inotify limits or unreliable on network filesystems | M | M | clear error with sysctl hint, `--poll` fallback, warnings | A8 |
| R8 | History DB corruption | L | M | WAL, `synchronous=FULL`, integrity check in `doctor`, corrupt DB renamed not deleted | A4 |
| R9 | Undo restores over or conflicts with newer user files | M | M | no-overwrite, origin-occupied handling, modified-file detection | A7 |
| R10 | Config edits corrupt a hand-written config | L | M | round-trip editing, validate-before-write, `.bak`, atomic replace | A1 |
| R11 | Running as root on Kali touches system files | M | H | hard refusal, denylist, realpath checks | A1, A12 |
| R12 | Scope creep (GUI, AI, extras) delays v1.0 | H | M | milestones, backlog, explicit out-of-scope list | Orchestrator |
| R13 | Parallel agents diverge or conflict | M | M | frozen contracts, file ownership table, wave merge gates, CONTRACT_CHANGE protocol | Orchestrator |
| R14 | Regex DoS in user rules | L | L | static nested-quantifier check, names only, length-capped | A2 |
| R15 | Non-UTF-8 file names break DB or output | M | M | path codec, `surrogateescape`-aware rendering | A4, A9 |
| R16 | Performance regressions on large folders | L | M | perf budgets in CI, no recursive scans in watch mode, precompiled rules | A11 |
| R17 | Dependency breakage (watchdog API changes) | L | M | lower-bound pins, CI matrix, isolation of watchdog use in `watch/` only | A8, A10 |
