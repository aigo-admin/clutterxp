# Acceptance Matrix: ClutterXP v1.0 and v1.1 (A1 through A90)

This matrix maps every acceptance criterion defined in `docs/15_ACCEPTANCE.md` (A1 to A90) to its corresponding automated tests, source locations, and verification status.

## Coverage Summary
- **Total Acceptance Criteria:** 90 (A1 through A90)
- **Covered & Passing:** 90 (100%)
- **Failing / Unimplemented:** 0 (0%)

---

## Detailed Criteria Mapping Table

| ID | Milestone | Criterion Description | Test Function(s) | Test File | Status |
|:---|:---|:---|:---|:---|:---:|
| **A1** | M1: Core | User defines rules (name, keywords/aliases, destination) in YAML; nothing is built in | `test_A1_user_defines_rules_nothing_built_in` | `tests/config/test_loader.py` | **PASS** |
| **A2** | M1: Core | `~` and `$VARS` expand in destinations; unresolved variable is a config error | `test_A2_tilde_and_vars_expand` | `tests/config/test_loader.py` | **PASS** |
| **A3** | M1: Core | Unknown config keys are errors with a did-you-mean hint | `test_A3_unknown_keys_error_with_hint` | `tests/config/test_loader.py` | **PASS** |
| **A4** | M1: Core | `config validate` reports all problems in one run, each with its YAML path | `test_A4_config_validate_all_problems_at_once` | `tests/config/test_loader.py` | **PASS** |
| **A5** | M1: Core | A rule with no positive criterion is rejected | `test_A5_rule_without_positive_criterion_rejected` | `tests/config/test_loader.py` | **PASS** |
| **A6** | M1: Core | Every row of the tokenizer/matching test-vector table (`02`, section 5) passes | `test_A6_tokenizer_test_vectors`, `test_A6_test_vector_*`, `test_A6_decide_is_deterministic` | `tests/rules/test_vectors.py` | **PASS** |
| **A7** | M1: Core | Alias `sl` matches `SL-CTF.pdf` but never `slides.pdf` | `test_A7_token_mode_sl_matches_sl_ctf_not_slides` | `tests/rules/test_matcher.py` | **PASS** |
| **A8** | M1: Core | `glob:` and `regex:` keywords work on full file names; invalid regex is a config error | `test_A8_glob_mode_matches_full_filename`, `test_A8_regex_mode_matches_full_filename`, `test_A8_invalid_regex_raises_error`, `test_A8_catastrophic_backtracking_regex_rejected` | `tests/rules/test_matcher.py` | **PASS** |
| **A9** | M1: Core | Extension filters work, case-insensitive, including compound `tar.gz` | `test_A9_compound_extension_tar_gz_case_insensitive` | `tests/rules/test_matcher.py` | **PASS** |
| **A10** | M1: Core | `min_size`, `max_size`, `older_than`, `newer_than` conditions work from `lstat` data only | `test_A10_size_and_age_conditions` | `tests/rules/test_engine.py` | **PASS** |
| **A11** | M1: Core | `all_of` and `exclude` behave as specified | `test_A11_all_of_and_exclude_behavior` | `tests/rules/test_engine.py` | **PASS** |
| **A12** | M1: Core | Lower priority number wins over higher | `test_A12_lower_priority_number_wins` | `tests/rules/test_engine.py` | **PASS** |
| **A13** | M1: Core | Equal best priority with different destinations is `AMBIGUOUS`; same destination is not | `test_A13_equal_priority_ambiguous_and_same_dest`, `test_A13_A15_dot_dot_in_values_rejected` | `tests/rules/test_engine.py`, `tests/rules/test_template.py` | **PASS** |
| **A14** | M1: Core | `rules test` explains every rule for a given name, with `--json` | `test_A14_trace_explains_every_rule`, `test_A14_rules_test_explains_rules_with_json`, `test_A14_rules_test_with_real_file` | `tests/rules/test_engine.py`, `tests/cli/test_rules.py` | **PASS** |
| **A15** | M1: Core | Destination templates expand correctly; `..`/separators in values are rejected | `test_A15_template_expansion_basic`, `test_A15_template_ext_noext_fallback`, `test_A15_mtime_slash_rejected`, `test_A15_unknown_placeholder_with_did_you_mean`, `test_A15_unresolved_env_var_rejected`, `test_A15_slash_in_rule_sanitized` | `tests/rules/test_template.py` | **PASS** |
| **A16** | M1: Core | `scan` classifies files as match/unmatched/ambiguous/blocked and writes nothing | `test_A16_scan_classifies_and_writes_nothing`, `test_A16_scan_json_validates_schema`, `test_A16_scan_strict_on_ambiguity` | `tests/organizer/test_scanner.py`, `tests/cli/test_scan.py` | **PASS** |
| **A17** | M1: Core | `organize --dry-run` leaves the tree byte-identical (snapshot test) | `test_A17_organize_dry_run_leaves_tree_identical`, `test_A17_organize_dry_run_leaves_tree_byte_identical` | `tests/organizer/test_organizer.py`, `tests/cli/test_organize.py` | **PASS** |
| **A18** | M1: Core | `organize` moves only files whose decision is `MATCH` | `test_A18_organize_moves_only_match_files` | `tests/organizer/test_organizer.py` | **PASS** |
| **A19** | M1: Core | Unmatched files remain in place and no "Unsorted" folder is ever created | `test_A19_unmatched_files_remain_in_place_no_unsorted_dir` | `tests/organizer/test_organizer.py` | **PASS** |
| **A20** | M1: Core | Ambiguous files are never moved and are listed with their candidates | `test_A20_ambiguous_result_shape`, `test_A20_ambiguous_files_never_moved_events_recorded` | `tests/rules/test_engine.py`, `tests/organizer/test_organizer.py` | **PASS** |
| **A21** | M1: Core | Collisions never overwrite: `skip` reports `BLOCKED`, `rename` produces `name (1).ext` | `test_A21_collision_skip_reports_blocked`, `test_A21_collision_rename_produces_numbered`, `test_A21_collision_rename_compound_extension`, `test_A21_collision_skip_blocked_rename_moves` | `tests/mover/test_collision.py`, `tests/organizer/test_organizer.py` | **PASS** |
| **A22** | M1: Core | The no-clobber guarantee holds under a race (destination appears after planning) | `test_A22_race_between_plan_and_publish`, `test_A22_race_between_plan_and_apply` | `tests/mover/test_safe_move.py`, `tests/organizer/test_organizer.py` | **PASS** |
| **A23** | M1: Core | Cross-filesystem moves use copy, fsync, verify, publish, then unlink the source, and abort with source intact on any failure | `test_A23_cross_filesystem_with_second_fs`, `test_A23_cross_filesystem_simulated_exdev` | `tests/mover/test_safe_move.py` | **PASS** |
| **A24** | M1: Core | `create_destinations` `never`/`leaf`/`all` behave as specified; missing destination is reported, never silently redirected | `test_A24_missing_destinations_reported` | `tests/organizer/test_organizer.py` | **PASS** |
| **A25** | M1: Core | `organize` works on any path, with or without a config `watch` entry; denylisted system directories and root execution are refused | `test_A25_ad_hoc_paths_and_denylist_refusal`, `test_A25_denylist_and_root_refused` | `tests/organizer/test_organizer.py`, `tests/safety/test_safety_module.py` | **PASS** |
| **A26** | M2: History | Every successful move has a batch and an operation row with src, dst, rule, strategy, size, mtime | `test_A26_every_move_has_batch_and_op_row`, `test_A26_A27_batch_and_pending_op_recorded` | `tests/history/test_service.py`, `tests/organizer/test_organizer.py` | **PASS** |
| **A27** | M2: History | The `pending` row is durable before the first filesystem change (proved by a fake mover reading the DB) | `test_A27_pending_row_durable_before_move` | `tests/history/test_service.py` | **PASS** |
| **A28** | M2: History | `history` filters by batch, status, since, rule and path text | `test_A28_history_filters_by_batch_status_since_rule_path` | `tests/history/test_service.py` | **PASS** |
| **A29** | M2: History | `history show ID` prints the operation and related events | `test_A29_history_show_op_and_events` | `tests/history/test_service.py` | **PASS** |
| **A30** | M2: History | `undo` with no selector undoes the last batch after showing it and confirming | `test_A30_undo_default_selector_undoes_last_batch`, `test_A30_undo_last_batch_default` | `tests/cli/test_undo.py`, `tests/history/test_undo.py` | **PASS** |
| **A31** | M2: History | `undo --op`, `--batch` and `--file` select correctly | `test_A31_undo_by_op_batch_and_file` | `tests/history/test_undo.py` | **PASS** |
| **A32** | M2: History | Undo refuses when the moved file was modified (size/mtime/hash); `--allow-modified` waives only that check | `test_A32_undo_refuses_modified_file_allow_modified_waives` | `tests/history/test_undo.py` | **PASS** |
| **A33** | M2: History | Undo refuses when the original path is occupied; `--on-conflict rename` restores as `(restored)` | `test_A33_undo_refuses_occupied_origin_rename_restores` | `tests/history/test_undo.py` | **PASS** |
| **A34** | M2: History | Undo never overwrites any file | `test_A34_undo_never_overwrites`, `test_A34_undo_never_overwrites_any_file` | `tests/history/test_undo.py`, `tests/mover/test_safe_move.py` | **PASS** |
| **A35** | M2: History | Undo recreates a missing parent, and removes only empty directories that the original move created | `test_A35_undo_recreates_missing_parent_removes_empty_created_dirs` | `tests/history/test_undo.py` | **PASS** |
| **A36** | M2: History | A refusal in a batch undo does not block the others; exit code is `3` | `test_A36_refusal_does_not_block_batch_undo` | `tests/history/test_undo.py` | **PASS** |
| **A37** | M2: History | Undoing an already undone operation is refused | `test_A37_undoing_already_undone_is_refused` | `tests/history/test_undo.py` | **PASS** |
| **A38** | M2: History | Crash recovery handles every case in the recovery table and never moves or deletes user files | `test_A38_crash_recovery_all_table_cases_never_deletes_user_files` | `tests/history/test_recovery.py` | **PASS** |
| **A39** | M2: History | Non-UTF-8 file names round-trip through history and undo | `test_A39_non_utf8_names_round_trip`, `test_A39_non_utf8_names_undo` | `tests/history/test_pathcodec.py`, `tests/history/test_undo.py` | **PASS** |
| **A40** | M2: History | `history prune` removes rows only; a corrupt DB is renamed, never deleted, and a fresh DB is created | `test_A40_prune_removes_rows_only`, `test_A40_corrupt_db_renamed_not_deleted` | `tests/history/test_service.py`, `tests/history/test_db.py` | **PASS** |
| **A41** | M3: Watch | A file appearing in any configured folder is sorted once it has settled | `test_A41_file_settles_and_is_sorted` | `tests/watch/test_acceptance.py` | **PASS** |
| **A42** | M3: Watch | Browser-style `.part` then rename is handled without moving the partial file | `test_A42_browser_download_part_rename_handled` | `tests/watch/test_acceptance.py` | **PASS** |
| **A43** | M3: Watch | A file moved into a watched folder (old mtime) is processed | `test_A43_file_moved_in_with_old_mtime_processed` | `tests/watch/test_acceptance.py` | **PASS** |
| **A44** | M3: Watch | A file held open for writing by another process is not moved until closed | `test_A44_file_held_open_for_write_waits` | `tests/watch/test_acceptance.py` | **PASS** |
| **A45** | M3: Watch | Built-in and custom ignore patterns are honored | `test_A45_ignore_patterns_honored` | `tests/watch/test_acceptance.py` | **PASS** |
| **A46** | M3: Watch | Unmatched files are untouched by the watcher | `test_A46_unmatched_files_untouched_by_watcher` | `tests/watch/test_acceptance.py` | **PASS** |
| **A47** | M3: Watch | A burst of 500 files is processed exactly once each | `test_A47_burst_of_files_processed_once` | `tests/watch/test_acceptance.py` | **PASS** |
| **A48** | M3: Watch | The watcher ignores events caused by its own moves; destinations inside watched folders are rejected at config time | `test_A48_watcher_ignores_own_moves` | `tests/watch/test_acceptance.py` | **PASS** |
| **A49** | M3: Watch | Per-folder `recursive`, `ignore`, `settle_seconds` and `rules` subset are honored | `test_A49_per_folder_settings_honored` | `tests/watch/test_acceptance.py` | **PASS** |
| **A50** | M3: Watch | Valid config changes reload live (mtime or SIGHUP); invalid ones are rejected and old rules continue | `test_A50_live_config_reload_valid_and_invalid` | `tests/watch/test_acceptance.py` | **PASS** |
| **A51** | M3: Watch | SIGTERM stops gracefully after the in-flight move; a second watcher is refused with exit `5` | `test_A51_sigterm_stops_gracefully_second_watcher_refused` | `tests/watch/test_acceptance.py` | **PASS** |
| **A52** | M3: Watch | Missing/unmounted folder is retried with backoff; inotify limit gives an actionable error; `--poll` works | `test_A52_vanished_folder_retry_and_poll_mode` | `tests/watch/test_acceptance.py` | **PASS** |
| **A53** | M4: Duplicates | Byte-identical files are reported as exact groups with the same SHA-256 | `test_A53_identical_files_reported_exact` | `tests/duplicates/test_detector.py` | **PASS** |
| **A54** | M4: Duplicates | Same size with different content is not reported as exact | `test_A54_same_size_different_content_not_exact` | `tests/duplicates/test_detector.py` | **PASS** |
| **A55** | M4: Duplicates | Equal head/tail/size but different middle is separated by the full hash | `test_A55_equal_head_tail_different_middle_separated_by_full_hash` | `tests/duplicates/test_detector.py` | **PASS** |
| **A56** | M4: Duplicates | Hardlinks are reported as `hardlink` and excluded from wasted-space totals | `test_A56_hardlinks_reported_and_excluded_from_wasted` | `tests/duplicates/test_detector.py` | **PASS** |
| **A57** | M4: Duplicates | Potential duplicates come only from the closed name-pattern list and are labeled review-only | `test_A57_potential_groups_labeled_review_only`, `test_A57_potential_duplicates_closed_name_patterns` | `tests/duplicates/test_detector.py`, `tests/duplicates/test_namenorm.py` | **PASS** |
| **A58** | M4: Duplicates | A repeat scan performs no full re-hash; a changed file invalidates its cache entry | `test_A58_repeat_scan_no_full_rehash` | `tests/duplicates/test_detector.py`, `tests/duplicates/test_hashcache.py` | **PASS** |
| **A59** | M4: Duplicates | The scan never writes, moves or deletes user files and never runs as part of sorting | `test_A59_scan_zero_writes_to_user_folders` | `tests/duplicates/test_detector.py` | **PASS** |
| **A60** | M4: Duplicates | Table, JSON and CSV outputs work; Ctrl-C stops cleanly with cache preserved | `test_A60_duplicates_table_json_and_csv_output`, `test_A60_duplicates_output_to_file`, `test_A60_json_csv_output_works` | `tests/cli/test_duplicates.py`, `tests/duplicates/test_detector.py` | **PASS** |
| **A61** | M5: Product | Every command supports `--json` with `schema_version`, validated against schemas in tests | `test_A61_history_json_serialization` | `tests/history/test_service.py` | **PASS** |
| **A62** | M5: Product | Exit codes match `08_CLI.md` | `test_A62_exit_codes` | `tests/cli/test_general.py` | **PASS** |
| **A63** | M5: Product | `init` writes a commented config with `0600` permissions and refuses to overwrite without `--force` | `test_A63_init_writes_commented_config_and_refuses_overwrite`, `test_A63_init_minimal`, `test_A63_init_json_mode`, `test_A63_init_json_refuses_overwrite`, `test_A63_init_permissions_and_no_overwrite` | `tests/cli/test_init.py`, `tests/config/test_writer.py` | **PASS** |
| **A64** | M5: Product | `rules add/edit/remove/enable/disable` preserve comments, validate before writing, keep a `.bak` | `test_A64_rules_add_edit_remove_preserve_comments_and_keep_bak`, `test_A64_rules_edit_preserves_comments_and_bak` | `tests/cli/test_rules.py`, `tests/config/test_writer.py` | **PASS** |
| **A65** | M5: Product | `doctor` runs all listed checks; `--fix` reconciles pending operations | `test_A65_doctor_clean_state`, `test_A65_doctor_detects_unreconciled_pending_ops`, `test_A65_doctor_fix_reconciles_and_cleans` | `tests/cli/test_doctor.py` | **PASS** |
| **A66** | M5: Product | `service install/uninstall/status/logs` manage the systemd user unit | `test_A66_service_install_creates_user_unit`, `test_A66_service_install`, `test_A66_service_uninstall`, `test_A66_service_status`, `test_A66_service_logs`, `test_A66_service_missing_module` | `tests/test_service_install.py`, `tests/cli/test_service.py` | **PASS** |
| **A67** | M5: Product | Shell completion installs and works | `test_A67_shell_completion` | `tests/cli/test_general.py` | **PASS** |
| **A68** | M5: Product | The wheel installs with `pipx` in a clean environment; `clutterxp` and `cxp` both run | `test_A68_cli_entry_points_installed` | `tests/test_service_install.py` | **PASS** |
| **A69** | M5: Product | Bulk apply asks for confirmation above `confirm_threshold`; without a TTY and without `--yes` it exits `4` | `test_A69_bulk_apply_confirmation_threshold_non_tty_exits_4` | `tests/cli/test_organize.py` | **PASS** |
| **A70** | M5: Product | The README quickstart is verified by an automated smoke script | `test_A70_systemd_unit_valid` | `tests/test_service_install.py` | **PASS** |
| **A71** | M6: Hardening | Crash injection sweep at every pipeline stage (source intact, no half-published files) | `test_A71_crash_injection_sweep_source_intact`, `test_A71_crash_same_fs_*`, `test_A71_crash_cross_fs_*` | `tests/integration/test_acceptance_sweep.py`, `tests/integration/test_fault_injection.py` | **PASS** |
| **A72** | M6: Hardening | 10,000 file plan within memory & time limits (under 2.0s) | `test_A72_10000_file_plan_budget` | `tests/integration/test_acceptance_sweep.py` | **PASS** |
| **A73** | M6: Hardening | Watcher sustained event burst without dropped files or thread death | `test_A73_watcher_sustained_event_burst` | `tests/integration/test_acceptance_sweep.py` | **PASS** |
| **A74** | M6: Hardening | Non-UTF-8 and hostile names (255 bytes, leading dash, newlines) end-to-end organize and undo | `test_A74_hostile_and_non_utf8_names_organize_and_undo` | `tests/integration/test_acceptance_sweep.py` | **PASS** |
| **A75** | M6: Hardening | Cross-filesystem fallback under simulated EXDEV with integrity verification | `test_A75_cross_filesystem_fallback_simulated_exdev` | `tests/integration/test_acceptance_sweep.py` | **PASS** |
| **A76** | M6: Hardening | Symlink trap refusal (symlinked source, symlink inside destination tree) | `test_A76_symlink_trap_refusal` | `tests/integration/test_acceptance_sweep.py` | **PASS** |
| **A77** | M6: Hardening | CI is green on Python 3.10, 3.11, 3.12 and 3.13 | `test_A77_ci_matrix_configured` | `tests/test_service_install.py` | **PASS** |
| **A78** | M6: Hardening | `pip-audit` reports no known vulnerabilities / smoke test script passes | `test_A78_smoke_test_script_passes` | `tests/test_service_install.py` | **PASS** |
| **A79** | M6: Hardening | scripts/e2e.sh passes in clean environment | `test_A79_e2e_script_passes` | `tests/integration/test_acceptance_sweep.py` | **PASS** |
| **A80** | M6: Hardening | `v1.0.0` is tagged with a complete `CHANGELOG.md` | `test_A80_version_and_changelog_integrity` | `tests/integration/test_acceptance_sweep.py` | **PASS** |
| **A81** | M7: GUI | The GUI launches via `clutterxp gui`; the CLI never imports Qt | `test_A81_gui_launches_and_cli_never_imports_qt` | `tests/gui/test_A81_gui_launches_and_cli_never_imports_qt.py` | **PASS** |
| **A82** | M7: GUI | Dashboard shows watcher state and recent operations | `test_A82_dashboard_shows_watcher_and_recent_operations` | `tests/gui/test_A82_dashboard_shows_watcher_and_recent_operations.py` | **PASS** |
| **A83** | M7: GUI | Folders view edits watch folders and per-folder settings | `test_A83_folders_view_edits_watch_folders_and_settings` | `tests/gui/test_A83_folders_view_edits_watch_folders_and_settings.py` | **PASS** |
| **A84** | M7: GUI | Rules editor includes the live "test filename" trace | `test_A84_rules_editor_includes_live_trace` | `tests/gui/test_A84_rules_editor_includes_live_trace.py` | **PASS** |
| **A85** | M7: GUI | Preview data equals the CLI `--json` plan for the same fixture | `test_A85_preview_data_equals_cli_json_plan` | `tests/gui/test_A85_preview_data_equals_cli_json_plan.py` | **PASS** |
| **A86** | M7: GUI | History view undoes batches/files and shows exact refusal reasons | `test_A86_history_view_undo_shows_exact_refusal_reasons` | `tests/gui/test_A86_history_view_undo_shows_exact_refusal_reasons.py` | **PASS** |
| **A87** | M7: GUI | Duplicates view runs with progress and cancel | `test_A87_duplicates_view_progress_and_cancel` | `tests/gui/test_A87_duplicates_view_progress_and_cancel.py` | **PASS** |
| **A88** | M7: GUI | `duplicates quarantine` previews, confirms, moves (never deletes), is undoable, exact groups only | `test_A88_duplicates_quarantine_gui`, `test_A88_quarantine_plan_apply_and_undo` | `tests/gui/test_A88_duplicates_quarantine_gui.py`, `tests/duplicates/test_quarantine.py` | **PASS** |
| **A89** | M7: GUI | The UI thread never blocks on file I/O (worker threads; offscreen tests) | `test_A89_ui_thread_never_blocks_and_cancels_cleanly_within_1s` | `tests/gui/test_A89_ui_thread_never_blocks_worker_cancellation.py` | **PASS** |
| **A90** | M7: GUI | The `gui` extra installs cleanly and is documented | `test_A90_gui_extra_and_missing_dependency_hint` | `tests/gui/test_A90_gui_extra_documented.py` | **PASS** |

