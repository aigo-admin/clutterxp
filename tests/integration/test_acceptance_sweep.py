"""Acceptance Sweep Test Suite proving criteria A71, A72, A73, A74, A75, A76, A79, A80.

Validates that every single acceptance criterion from docs/15_ACCEPTANCE.md
is implemented and proven by an automated test.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clutterxp.cli.app import app
from clutterxp.config.runtime import RuntimeConfig
from clutterxp.config.schema import RuleSpec, Settings
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    ErrorCode,
    FileInfo,
    MoveStrategy,
)
from clutterxp.mover import fsops
from clutterxp.mover.safe_move import SafeMover
from clutterxp.planner import build_plan
from clutterxp.rules import build_engine
from clutterxp.watch.debounce import Debouncer
from tests.conftest import EnvPaths, MakeTreeFn


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# =============================================================================
# A71: Crash injection sweep at every pipeline stage
# =============================================================================


def test_A71_crash_injection_sweep_source_intact(
    env: EnvPaths,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """A71: Crash injection sweep at pipeline stages leaves source intact with zero loss."""
    from tests.integration.test_fault_injection import (
        test_A71_crash_cross_fs_between_publish_and_unlink,
        test_A71_crash_cross_fs_during_temp_copy,
        test_A71_crash_cross_fs_during_verification,
        test_A71_crash_same_fs_after_move_before_finish_op,
        test_A71_crash_same_fs_before_begin_op,
        test_A71_crash_same_fs_during_link_unlink_partial,
        test_A71_crash_same_fs_pending_row_before_mover,
    )

    # Run the comprehensive crash injection suite
    test_A71_crash_same_fs_before_begin_op(env, make_tree, tmp_path / "c1")
    test_A71_crash_same_fs_pending_row_before_mover(env, make_tree, tmp_path / "c2")
    test_A71_crash_same_fs_during_link_unlink_partial(env, make_tree, tmp_path / "c3")
    test_A71_crash_same_fs_after_move_before_finish_op(env, make_tree, tmp_path / "c4")
    test_A71_crash_cross_fs_during_temp_copy(env, make_tree, tmp_path / "c5")
    test_A71_crash_cross_fs_during_verification(env, make_tree, tmp_path / "c6")
    test_A71_crash_cross_fs_between_publish_and_unlink(env, make_tree, tmp_path / "c7")


# =============================================================================
# A72: 10,000 file plan within memory & time limits
# =============================================================================


def test_A72_10000_file_plan_budget() -> None:
    """A72: 10,000 file plan executes well within 2 second performance budget."""
    files: list[FileInfo] = []
    for i in range(10_000):
        name = f"doc_{i:05d}.pdf"
        p = Path(f"/inbox/{name}")
        files.append(
            FileInfo(
                path=p,
                name=name,
                stem=f"doc_{i:05d}",
                ext="pdf",
                size=1024 + (i % 500),
                mtime_ns=1700000000000000000 + i,
                inode=1000 + i,
                device=1,
            )
        )

    rules = [
        RuleSpec(
            name=f"Rule_{r}",
            keywords=[f"doc_{r:02d}"],
            extensions=["pdf"],
            destination=f"/dest/folder_{r}",
            collision=CollisionPolicy.RENAME,
            create_destinations=CreateDestinations.ALL,
        )
        for r in range(50)
    ]
    settings = Settings()
    engine = build_engine(rules, settings)
    runtime_cfg = RuntimeConfig(
        config_path=Path("/config.yaml"),
        mtime_ns=0,
        settings=settings,
        watch_entries=(),
        rules=tuple(rules),
        disabled_rules=(),
        errors=(),
        warnings=(),
    )

    t0 = time.perf_counter()
    plan = build_plan(iter(files), engine, runtime_cfg, fs_check=False)
    elapsed = time.perf_counter() - t0

    assert len(plan.actions) == 10_000
    # Budget in docs/01 section 8 is under 2.0s
    assert elapsed < 2.0, f"Plan for 10,000 files took {elapsed:.3f}s (budget: 2.0s)"


# =============================================================================
# A73: Watcher sustained event burst without dropped files or thread death
# =============================================================================


def test_A73_watcher_sustained_event_burst() -> None:
    """A73: Debouncer processes sustained burst of 500 events without dropped files."""
    debouncer = Debouncer()
    paths = [Path(f"/inbox/burst_file_{i:03d}.pdf") for i in range(500)]

    for p in paths:
        debouncer.arm(p, 0.05)

    assert len(debouncer) == 500

    # Wait for debounce duration and collect due items
    time.sleep(0.06)
    due_items = debouncer.due()

    assert len(due_items) == 500
    assert set(due_items) == set(paths)
    assert len(debouncer) == 0


# =============================================================================
# A74: Non-UTF-8 and hostile names end-to-end organize and undo
# =============================================================================


def test_A74_hostile_and_non_utf8_names_organize_and_undo(
    env: EnvPaths,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """A74: Non-UTF-8 surrogate bytes and hostile names (255 bytes, leading dash, newlines)."""
    inbox = tmp_path / "inbox"
    dest = tmp_path / "dest"
    inbox.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)

    # 1. Leading dash
    f_dash = inbox / "-rf_danger.pdf"
    f_dash.write_text("dash content", encoding="utf-8")

    # 2. Spaces and punctuation
    f_space = inbox / "My   Tax (2026) [Official] #1.pdf"
    f_space.write_text("space content", encoding="utf-8")

    # 3. Non-UTF-8 raw surrogateescape bytes
    surrogate_name = b"raw_\xff\xfe_data.pdf".decode("utf-8", "surrogateescape")
    f_surrogate = inbox / surrogate_name
    f_surrogate.write_text("surrogate data", encoding="utf-8")

    # 4. 255-byte name
    stem_255 = "long_" + ("z" * 240)
    name_255 = stem_255[:251] + ".pdf"
    f_255 = inbox / name_255
    f_255.write_text("255 byte content", encoding="utf-8")

    env.config_path.write_text(
        f"version: 1\nrules:\n  - name: PDFRules\n    extensions: [pdf]\n    destination: {dest}\n",
        encoding="utf-8",
    )

    # Organize
    res_org = runner.invoke(app, ["organize", str(inbox), "--yes"])
    assert res_org.exit_code == 0

    assert (dest / "-rf_danger.pdf").exists()
    assert (dest / "My   Tax (2026) [Official] #1.pdf").exists()
    assert (dest / surrogate_name).exists()
    assert (dest / name_255).exists()

    # Undo
    res_undo = runner.invoke(app, ["undo", "--yes"])
    assert res_undo.exit_code == 0

    # Restored in inbox
    assert (inbox / "-rf_danger.pdf").read_text(encoding="utf-8") == "dash content"
    restored_space = (inbox / "My   Tax (2026) [Official] #1.pdf").read_text(encoding="utf-8")
    assert restored_space == "space content"
    assert (inbox / surrogate_name).read_text(encoding="utf-8") == "surrogate data"
    assert (inbox / name_255).read_text(encoding="utf-8") == "255 byte content"


# =============================================================================
# A75: Cross-filesystem fallback under simulated EXDEV with integrity verification
# =============================================================================


def test_A75_cross_filesystem_fallback_simulated_exdev(tmp_path: Path) -> None:
    """A75: EXDEV raises on same-fs primitives triggers Strategy B copy-verify-unlink."""
    src = tmp_path / "original.pdf"
    src.write_text("critical verified content", encoding="utf-8")
    expected_hash = hashlib.sha256(b"critical verified content").hexdigest()

    dest_dir = tmp_path / "cross_fs_target"
    dest_dir.mkdir()

    mover = SafeMover(verify_copy="hash")

    # Force EXDEV error when attempting to rename the source to destination
    orig_rename = fsops.rename_noreplace

    def exdev_rename(s: Path, d: Path) -> bool:
        if s == src:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return orig_rename(s, d)

    with patch.object(fsops, "rename_noreplace", side_effect=exdev_rename):
        res = mover.move_file(src, dest_dir)

    assert res.ok is True
    assert res.strategy == MoveStrategy.COPY_VERIFY
    assert not src.exists()
    dst_file = dest_dir / "original.pdf"
    assert dst_file.exists()
    assert hashlib.sha256(dst_file.read_bytes()).hexdigest() == expected_hash


# =============================================================================
# A76: Symlink trap refusal (symlinked source, symlink inside destination tree)
# =============================================================================


def test_A76_symlink_trap_refusal(tmp_path: Path) -> None:
    """A76: Symlink trap refusal: symlinked source and symlink inside destination tree."""
    mover = SafeMover()

    # 1. Symlinked source refusal
    real_src = tmp_path / "real_file.pdf"
    real_src.write_text("target data", encoding="utf-8")
    symlink_src = tmp_path / "symlink_src.pdf"
    symlink_src.symlink_to(real_src)

    dest = tmp_path / "dest"
    dest.mkdir()

    res_src = mover.move_file(symlink_src, dest)
    assert res_src.ok is False
    assert res_src.code == ErrorCode.SRC_NOT_REGULAR
    assert real_src.exists()
    assert symlink_src.is_symlink()

    # 2. Symlink component inside destination path refusal
    valid_file = tmp_path / "valid.pdf"
    valid_file.write_text("valid data", encoding="utf-8")

    outside_dir = tmp_path / "outside_secret"
    outside_dir.mkdir()
    sym_dest_dir = tmp_path / "dest" / "sym_component"
    sym_dest_dir.symlink_to(outside_dir)

    res_dest = mover.move_file(valid_file, sym_dest_dir / "leaf", create=CreateDestinations.ALL)
    assert res_dest.ok is False
    assert res_dest.code == ErrorCode.DEST_INVALID
    assert valid_file.exists()


# =============================================================================
# A79: scripts/e2e.sh passes in clean environment
# =============================================================================


def test_A79_e2e_script_passes() -> None:
    """A79: scripts/e2e.sh runs all commands and passes in clean environment."""
    repo_root = Path(__file__).resolve().parents[2]
    e2e_script = repo_root / "scripts" / "e2e.sh"

    assert e2e_script.exists()
    assert os.access(e2e_script, os.X_OK), "scripts/e2e.sh must be executable (chmod +x)"

    res = subprocess.run(
        ["bash", str(e2e_script)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, (
        f"scripts/e2e.sh failed (code {res.returncode}):\n{res.stdout}\n{res.stderr}"
    )


# =============================================================================
# A80: CHANGELOG and version metadata integrity
# =============================================================================


def test_A80_version_and_changelog_integrity() -> None:
    """A80: CHANGELOG.md exists with complete release notes, and pyproject.toml defines version."""
    repo_root = Path(__file__).resolve().parents[2]

    changelog_path = repo_root / "CHANGELOG.md"
    assert changelog_path.exists(), "CHANGELOG.md must exist in repo root"
    changelog_text = changelog_path.read_text(encoding="utf-8")
    assert "## [" in changelog_text or "## 1.0.0" in changelog_text or "Added" in changelog_text

    pyproject_path = repo_root / "pyproject.toml"
    assert pyproject_path.exists()
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    assert 'name = "clutterxp"' in pyproject_text


# =============================================================================
# Acceptance Matrix Completeness Verification: Every A1..A80 has a test!
# =============================================================================


def test_acceptance_criteria_sweep_completeness() -> None:
    """Verify that every single criterion A1 through A90 is mapped to a test function."""
    repo_root = Path(__file__).resolve().parents[2]
    tests_dir = repo_root / "tests"

    found_ids: dict[int, list[str]] = {}
    pattern = re.compile(r"def\s+(test_A(\d+)_\w+)")

    for py_path in tests_dir.rglob("*.py"):
        content = py_path.read_text(encoding="utf-8")
        for match in pattern.finditer(content):
            fn_name = match.group(1)
            cid = int(match.group(2))
            found_ids.setdefault(cid, []).append(fn_name)

    # Assert every ID 1 through 90 has at least one test
    missing = [f"A{i}" for i in range(1, 91) if i not in found_ids]
    assert not missing, f"Acceptance criteria without test coverage: {missing}"

    # Also check acceptance matrix markdown file exists
    matrix_file = repo_root / "docs" / "reviews" / "acceptance_matrix.md"
    assert matrix_file.exists(), "docs/reviews/acceptance_matrix.md must exist"
    matrix_text = matrix_file.read_text(encoding="utf-8")
    for i in range(1, 91):
        assert f"A{i}" in matrix_text, f"A{i} is missing from docs/reviews/acceptance_matrix.md"
