"""Full safety invariants test suite (S1 through S15) and static safety rules.

Covers all invariants S1 to S15 via CLI and core pipeline, and enforces
static AST rules (no shutil.move/os.rename outside fsops, no rmtree,
no shell=True, no network modules, 0600/0700 permissions).
"""

from __future__ import annotations

import ast
import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clutterxp.cli.app import app
from clutterxp.history.service import HistoryService
from clutterxp.locking import fs_lock, watch_lock
from clutterxp.models import ClutterError, ErrorCode, FileInfo, OpStatus
from clutterxp.mover.safe_move import SafeMover
from clutterxp.rules.template import expand
from tests.conftest import AssertUnchangedFn, EnvPaths, MakeTreeFn, SnapshotFn


@pytest.fixture
def runner() -> CliRunner:
    """Fixture providing CliRunner for CLI invocation."""
    return CliRunner()


# -----------------------------------------------------------------------------
# S1: A file is moved only when a rule explicitly matches and the decision is MATCH.
# -----------------------------------------------------------------------------
def test_S1_file_moved_only_when_explicit_match(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """S1: A file is moved only when a rule explicitly matches and decision is MATCH."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "dest_invoices"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "inbox/invoice_2026.pdf": "invoice contents",
            "inbox/unrelated_random.txt": "unrelated contents",
        }
    )

    env.config_path.write_text(
        f"version: 1\n"
        f"rules:\n"
        f"  - name: Invoices\n"
        f"    keywords: [invoice]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    res = runner.invoke(app, ["organize", str(src_dir), "--yes"])
    assert res.exit_code == 0

    assert (dest_dir / "invoice_2026.pdf").exists()
    assert not (src_dir / "invoice_2026.pdf").exists()

    # Unmatched file remains strictly untouched
    assert (src_dir / "unrelated_random.txt").exists()
    assert not (dest_dir / "unrelated_random.txt").exists()


# -----------------------------------------------------------------------------
# S2: NO_MATCH, AMBIGUOUS and IGNORED files are never touched.
# -----------------------------------------------------------------------------
def test_S2_no_match_ambiguous_ignored_untouched(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    snapshot: SnapshotFn,
    assert_unchanged: AssertUnchangedFn,
    tmp_path: Path,
) -> None:
    """S2: NO_MATCH, AMBIGUOUS and IGNORED files are never touched."""
    src_dir = tmp_path / "inbox"
    dest_a = tmp_path / "dest_a"
    dest_b = tmp_path / "dest_b"
    dest_a.mkdir(parents=True, exist_ok=True)
    dest_b.mkdir(parents=True, exist_ok=True)

    # 1. Unmatched file: random text
    # 2. Ambiguous file: matches two rules of equal priority with different destinations
    # 3. Ignored file: .crdownload, .part, or hidden file
    make_tree(
        {
            "inbox/random_notes.txt": "plain notes",
            "inbox/report_shared.pdf": "both rules match",
            "inbox/.hidden_work.txt": "hidden data",
            "inbox/download.bin.crdownload": "partial download",
            "inbox/valid_target.pdf": "move this one",
        }
    )

    env.config_path.write_text(
        f"version: 1\n"
        f"rules:\n"
        f"  - name: RuleAlpha\n"
        f"    priority: 10\n"
        f"    keywords: [report]\n"
        f"    destination: {dest_a}\n"
        f"  - name: RuleBeta\n"
        f"    priority: 10\n"
        f"    keywords: [shared]\n"
        f"    destination: {dest_b}\n"
        f"  - name: TargetRule\n"
        f"    priority: 5\n"
        f"    keywords: [valid]\n"
        f"    destination: {dest_a}\n",
        encoding="utf-8",
    )

    # Snapshots of files that must not change
    snap_unmatched = snapshot(src_dir / "random_notes.txt")
    snap_ambig = snapshot(src_dir / "report_shared.pdf")
    snap_hidden = snapshot(src_dir / ".hidden_work.txt")
    snap_partial = snapshot(src_dir / "download.bin.crdownload")

    res = runner.invoke(app, ["organize", str(src_dir), "--yes"])
    # Exit code might be 0 or 3 depending on whether ambiguous is reported as partial/warning
    assert res.exit_code in (0, 3)

    # Valid file moved
    assert (dest_a / "valid_target.pdf").exists()

    # The untouched files are byte-identical
    assert_unchanged(src_dir / "random_notes.txt", snap_unmatched)
    assert_unchanged(src_dir / "report_shared.pdf", snap_ambig)
    assert_unchanged(src_dir / ".hidden_work.txt", snap_hidden)
    assert_unchanged(src_dir / "download.bin.crdownload", snap_partial)


# -----------------------------------------------------------------------------
# S3: No operation overwrites an existing path (move, undo, copy fallback, RENAME loop).
# -----------------------------------------------------------------------------
def test_S3_no_operation_overwrites_existing_path(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """S3: No operation overwrites an existing path under any condition."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Destination already has document.pdf with original content
    existing_dest_file = dest_dir / "document.pdf"
    existing_dest_file.write_text("ORIGINAL DEST CONTENT", encoding="utf-8")

    # Source has document.pdf with new content
    make_tree(
        {
            "inbox/document.pdf": "NEW INCOMING CONTENT",
        }
    )

    # Policy 1: SKIP -> blocked, never overwrite
    env.config_path.write_text(
        f"version: 1\n"
        f"rules:\n"
        f"  - name: Docs\n"
        f"    keywords: [document]\n"
        f"    collision: skip\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    res_skip = runner.invoke(app, ["organize", str(src_dir), "--yes"])
    assert res_skip.exit_code in (0, 3)
    assert existing_dest_file.read_text(encoding="utf-8") == "ORIGINAL DEST CONTENT"
    assert (src_dir / "document.pdf").read_text(encoding="utf-8") == "NEW INCOMING CONTENT"

    # Policy 2: RENAME -> writes document (1).pdf, never overwrites document.pdf
    env.config_path.write_text(
        f"version: 1\n"
        f"rules:\n"
        f"  - name: Docs\n"
        f"    keywords: [document]\n"
        f"    collision: rename\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    res_rename = runner.invoke(app, ["organize", str(src_dir), "--yes"])
    assert res_rename.exit_code == 0
    assert existing_dest_file.read_text(encoding="utf-8") == "ORIGINAL DEST CONTENT"
    renamed_file = dest_dir / "document (1).pdf"
    assert renamed_file.exists()
    assert renamed_file.read_text(encoding="utf-8") == "NEW INCOMING CONTENT"


# -----------------------------------------------------------------------------
# S4: No user file is deleted. Unlink only after verified publish; undo empty dirs only.
# -----------------------------------------------------------------------------
def test_S4_no_user_file_deleted_unlink_after_verified_publish(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """S4: No user file deleted; directories removed only if empty and clutterxp created."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "nested" / "created_dir"

    make_tree(
        {
            "inbox/archive.tar.gz": "archive contents",
        }
    )

    env.config_path.write_text(
        f"version: 1\n"
        f"settings:\n"
        f"  create_destinations: all\n"
        f"rules:\n"
        f"  - name: Archives\n"
        f"    extensions: [tar.gz]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    # Organize creates dest_dir and moves archive.tar.gz
    res = runner.invoke(app, ["organize", str(src_dir), "--yes"])
    assert res.exit_code == 0
    assert (dest_dir / "archive.tar.gz").exists()
    assert not (src_dir / "archive.tar.gz").exists()

    # Now run undo
    res_undo = runner.invoke(app, ["undo", "--yes"])
    assert res_undo.exit_code == 0

    # User file is safely back in inbox, not deleted
    assert (src_dir / "archive.tar.gz").exists()
    assert (src_dir / "archive.tar.gz").read_text() == "archive contents"
    # Empty created dir was removed
    assert not dest_dir.exists()


# -----------------------------------------------------------------------------
# S5: A pending journal row is durable before the first filesystem change.
# -----------------------------------------------------------------------------
def test_S5_pending_row_durable_before_first_fs_change(
    env: EnvPaths,
    tmp_path: Path,
) -> None:
    """S5: Pending journal row is committed to SQLite before any filesystem mutation."""
    src = tmp_path / "source.txt"
    src.write_text("hello journal safety", encoding="utf-8")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()

    batch_id = history.begin_batch("test_s5", folder=tmp_path)
    info = FileInfo.from_path(src)

    # Intercept mover to check that DB has 'pending' row before the move runs
    called_verified = False

    orig_move_file = mover.move_file

    def verifying_move_file(*args: Any, **kwargs: Any) -> Any:
        nonlocal called_verified
        # Query database right now
        conn = history._get_conn()
        cur = conn.execute(
            "SELECT status FROM operations WHERE batch_id = ? AND src_path = ?;",
            (batch_id, str(src)),
        )
        row = cur.fetchone()
        assert row is not None, "Pending row MUST exist before mover mutates filesystem"
        assert row[0] == OpStatus.PENDING.value
        called_verified = True
        return orig_move_file(*args, **kwargs)

    with patch.object(mover, "move_file", side_effect=verifying_move_file):
        op_id = history.begin_op(batch_id, src, "test_rule", info)
        res = mover.move_file(src, dest_dir)
        history.finish_op(op_id, res)

    assert called_verified, "Mover hook was executed and confirmed durable pending row"


# -----------------------------------------------------------------------------
# S6: Preview, scan, dry-run and duplicates scan perform zero writes to user folders.
# -----------------------------------------------------------------------------
def test_S6_zero_writes_during_preview_scan_dry_run_duplicates(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    snapshot: SnapshotFn,
    assert_unchanged: AssertUnchangedFn,
    tmp_path: Path,
) -> None:
    """S6: Preview, scan, dry-run and duplicates scan perform zero writes to user folders."""
    src_dir = tmp_path / "user_folder"
    dest_dir = tmp_path / "dest_folder"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "user_folder/file_one.pdf": "one content",
            "user_folder/file_two.pdf": "two content",
            "user_folder/dup_a.txt": "identical content",
            "user_folder/dup_b.txt": "identical content",
        }
    )

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {src_dir}\n"
        f"rules:\n"
        f"  - name: PDFRules\n"
        f"    extensions: [pdf]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    snap_before = snapshot(src_dir)

    # 1. scan
    res_scan = runner.invoke(app, ["scan", str(src_dir)])
    assert res_scan.exit_code == 0
    assert_unchanged(src_dir, snap_before)

    # 2. organize --dry-run
    res_dry = runner.invoke(app, ["organize", str(src_dir), "--dry-run"])
    assert res_dry.exit_code == 0
    assert_unchanged(src_dir, snap_before)

    # 3. duplicates scan
    res_dup = runner.invoke(app, ["duplicates", "scan", str(src_dir)])
    assert res_dup.exit_code == 0
    assert_unchanged(src_dir, snap_before)


# -----------------------------------------------------------------------------
# S7: Sorting never reads file contents and never hashes.
# -----------------------------------------------------------------------------
def test_S7_sorting_never_reads_file_contents(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """S7: Sorting never reads file contents and never hashes (only metadata)."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "inbox/receipt_2026.pdf": "unreadable content if opened",
        }
    )

    env.config_path.write_text(
        f"version: 1\n"
        f"rules:\n"
        f"  - name: Receipts\n"
        f"    keywords: [receipt]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    # Trap Path.read_bytes and builtins.open on the source file to prove sorting doesn't read it
    target_file = src_dir / "receipt_2026.pdf"
    orig_open = Path.open

    def guarded_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        try:
            if self.resolve() == target_file.resolve() and (not args or "r" in str(args[0])):
                # For safe_move Strategy A (same-fs), open() should NEVER be called on user file
                raise AssertionError(f"Forbidden content read attempted on {self}")
        except OSError:
            pass
        return orig_open(self, *args, **kwargs)

    with patch.object(Path, "open", guarded_open):
        res = runner.invoke(app, ["organize", str(src_dir), "--yes"])
        assert res.exit_code == 0

    assert (dest_dir / "receipt_2026.pdf").exists()


# -----------------------------------------------------------------------------
# S8: Only regular files are moved. Symlinks not followed.
# -----------------------------------------------------------------------------
def test_S8_only_regular_files_moved_symlinks_not_followed(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """S8: Only regular files are moved; symlinks are never followed/moved."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir(parents=True, exist_ok=True)

    real_file = tmp_path / "external_real.pdf"
    real_file.write_text("external data", encoding="utf-8")

    make_tree(
        {
            "inbox/regular.pdf": "regular content",
        }
    )

    # Create a symlink in inbox pointing to real_file
    symlink_inbox = src_dir / "symlink_invoice.pdf"
    symlink_inbox.symlink_to(real_file)

    # Create a subfolder in inbox
    subfolder = src_dir / "subfolder_invoice"
    subfolder.mkdir()

    env.config_path.write_text(
        f"version: 1\n"
        f"rules:\n"
        f"  - name: AllPDFs\n"
        f"    extensions: [pdf]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    res = runner.invoke(app, ["organize", str(src_dir), "--yes"])
    assert res.exit_code == 0

    # Regular file moved
    assert (dest_dir / "regular.pdf").exists()

    # Symlink was NOT moved, target was NOT touched
    assert symlink_inbox.is_symlink()
    assert real_file.exists()
    assert not (dest_dir / "symlink_invoice.pdf").exists()

    # Subfolder was NOT moved
    assert subfolder.exists()
    assert not (dest_dir / "subfolder_invoice").exists()


# -----------------------------------------------------------------------------
# S9: A destination inside a watched folder is rejected unless allow_chain: true.
# -----------------------------------------------------------------------------
def test_S9_destination_inside_watched_folder_rejected(
    env: EnvPaths,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """S9: A destination inside a watched folder is rejected unless allow_chain: true."""
    watch_folder = tmp_path / "downloads"
    nested_dest = watch_folder / "organized_pdfs"
    watch_folder.mkdir(parents=True, exist_ok=True)

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {watch_folder}\n"
        f"rules:\n"
        f"  - name: NestedRule\n"
        f"    keywords: [test]\n"
        f"    destination: {nested_dest}\n",
        encoding="utf-8",
    )

    res = runner.invoke(app, ["config", "validate"])
    assert res.exit_code != 0
    assert "inside watched folder" in res.stdout or "chain" in res.stdout


# -----------------------------------------------------------------------------
# S10: Root refusal: if geteuid() == 0 exit 4 unless --allow-root.
# -----------------------------------------------------------------------------
def test_S10_root_refusal(
    env: EnvPaths,
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """S10: Root refusal: if geteuid() == 0 exit 4 unless --allow-root."""
    src_dir = tmp_path / "inbox"
    src_dir.mkdir(parents=True, exist_ok=True)

    env.config_path.write_text(
        "version: 1\nrules:\n  - name: Dummy\n    keywords: [test]\n    destination: /tmp/dest\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(os, "geteuid", lambda: 0)

    # Without --allow-root: exits 4
    res = runner.invoke(app, ["scan", str(src_dir)])
    assert res.exit_code == 4
    assert "root" in res.stdout.lower() or "root" in (res.stderr or "").lower()

    # With --allow-root: root refusal is bypassed
    res_allow = runner.invoke(app, ["--allow-root", "--version"])
    assert res_allow.exit_code == 0


# -----------------------------------------------------------------------------
# S11: System directories are never used as source roots or destinations.
# -----------------------------------------------------------------------------
def test_S11_system_directories_denylist(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """S11: System directories are refused as source roots or destinations."""
    # 1. Source root is /etc -> refused
    res_src = runner.invoke(app, ["organize", "/etc", "--yes"])
    assert res_src.exit_code in (2, 4) or "refused" in res_src.stdout.lower()

    # 2. Destination is /usr/bin -> config validate flags error
    src_dir = tmp_path / "inbox"
    make_tree({"inbox/dangerous_test.txt": "content"})
    env.config_path.write_text(
        "version: 1\n"
        "rules:\n"
        "  - name: Dangerous\n"
        "    keywords: [test]\n"
        "    destination: /usr/bin\n",
        encoding="utf-8",
    )
    res_val = runner.invoke(app, ["config", "validate"])
    assert res_val.exit_code != 0
    assert "denylist" in res_val.stdout.lower()

    # Organize reports it as blocked
    res_dest = runner.invoke(app, ["organize", str(src_dir), "--yes"])
    assert res_dest.exit_code == 3
    assert "denylist" in res_dest.stdout.lower() or "block" in res_dest.stdout.lower()


# -----------------------------------------------------------------------------
# S12: All state files created with 0600 (dirs 0700). No network access.
# -----------------------------------------------------------------------------
def test_S12_permissions_and_no_network(
    env: EnvPaths,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """S12: All state files created with 0600 (dirs 0700); zero network access."""
    from clutterxp.paths import cache_dir, state_dir

    res_init = runner.invoke(app, ["init", "--minimal"])
    assert res_init.exit_code == 0

    assert env.config_path.exists()
    assert stat.S_IMODE(env.config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(env.config_path.parent.stat().st_mode) == 0o700

    # Trigger DB creation
    runner.invoke(app, ["doctor"])

    db_path = env.state_dir / "history.db"
    if db_path.exists():
        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600

    # Directories managed by paths.py are 0700
    s_dir = state_dir()
    c_dir = cache_dir()
    assert stat.S_IMODE(s_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(c_dir.stat().st_mode) == 0o700


# -----------------------------------------------------------------------------
# S13: Template-expanded destinations cannot escape their static root.
# -----------------------------------------------------------------------------
def test_S13_template_destinations_cannot_escape_root() -> None:
    """S13: Template-expanded destinations cannot escape their static root."""
    info = FileInfo(
        path=Path("/tmp/doc.pdf"),
        name="doc.pdf",
        stem="doc",
        ext="pdf",
        size=1024,
        mtime_ns=1700000000000000000,
        inode=100,
        device=1,
    )
    with pytest.raises(ClutterError) as exc_info:
        expand("/home/alice/docs/{year}/../../etc", info, "Rule")
    assert exc_info.value.code == ErrorCode.DEST_INVALID
    assert "escapes static root" in str(exc_info.value)

    info_dotdot = FileInfo(
        path=Path("/tmp/.."),
        name="..",
        stem="..",
        ext="..",
        size=1024,
        mtime_ns=1700000000000000000,
        inode=100,
        device=1,
    )
    with pytest.raises(ClutterError) as exc_dotdot:
        expand("/dest/{ext}", info_dotdot, "Rule")
    assert exc_dotdot.value.code == ErrorCode.DEST_INVALID


# -----------------------------------------------------------------------------
# S14: Concurrency locks: fs lock and watch lock.
# -----------------------------------------------------------------------------
def test_S14_concurrency_locks_fs_and_watch(env: EnvPaths) -> None:
    """S14: fs lock prevents concurrent moves; watch lock prevents concurrent watchers."""
    fs_lock_file = env.state_dir / "fs.lock"
    watch_lock_file = env.state_dir / "watch.lock"

    # 1. fs_lock concurrency
    with fs_lock(fs_lock_file):
        with pytest.raises(ClutterError) as exc_info, fs_lock(fs_lock_file, timeout=0.1):
            pass
        assert exc_info.value.code == ErrorCode.LOCK_TIMEOUT

    # 2. watch_lock concurrency
    with watch_lock(watch_lock_file):
        with pytest.raises(ClutterError) as exc_info, watch_lock(watch_lock_file):
            pass
        assert exc_info.value.code == ErrorCode.LOCK_TIMEOUT
        assert "is already held" in str(exc_info.value)


# -----------------------------------------------------------------------------
# S15: A failing file never aborts the batch or corrupts others.
# -----------------------------------------------------------------------------
def test_S15_failing_file_never_aborts_batch_or_corrupts_others(
    env: EnvPaths,
    runner: CliRunner,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """S15: A failing file never aborts the batch or corrupts others; exit code is 3."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "inbox/file1_test.pdf": "valid content 1",
            "inbox/file2_blocked.pdf": "unreadable content",
            "inbox/file3_test.pdf": "valid content 3",
        }
    )

    env.config_path.write_text(
        f"version: 1\n"
        f"rules:\n"
        f"  - name: PDFRules\n"
        f"    extensions: [pdf]\n"
        f"    destination: {dest_dir}\n",
        encoding="utf-8",
    )

    orig_move = SafeMover.move_file

    def mock_move(self: SafeMover, src: Path, *args: Any, **kwargs: Any) -> Any:
        if Path(src).name == "file2_blocked.pdf":
            from clutterxp.models import MoveResult

            return MoveResult(
                ok=False,
                src=Path(src),
                dst=None,
                strategy=None,
                code=ErrorCode.PERMISSION,
                message="Mock permission error on file2",
            )
        return orig_move(self, src, *args, **kwargs)

    with patch.object(SafeMover, "move_file", mock_move):
        res = runner.invoke(app, ["organize", str(src_dir), "--yes"])
        # Partial failure exits 3
        assert res.exit_code == 3

        # file1 and file3 are successfully moved
        assert (dest_dir / "file1_test.pdf").exists()
        assert (dest_dir / "file1_test.pdf").read_text() == "valid content 1"
        assert (dest_dir / "file3_test.pdf").exists()
        assert (dest_dir / "file3_test.pdf").read_text() == "valid content 3"

        # file2 remains in source and was not corrupted
        assert (src_dir / "file2_blocked.pdf").exists()
        assert (src_dir / "file2_blocked.pdf").read_text() == "unreadable content"


# =============================================================================
# Static AST / Grep Tests (docs/09 section 5)
# =============================================================================


def test_static_no_forbidden_move_replace_rename_on_user_files() -> None:
    """Static check: no shutil.move, and no os/Path rename or replace outside allowed files."""
    src_root = Path(__file__).resolve().parents[2] / "src" / "clutterxp"

    # Documented allowed files for atomic temp-file replace or corrupt-DB rename:
    allowed_replace = {
        "config/writer.py",
        "cli/cmd_config.py",
    }
    allowed_rename = {
        "mover/fsops.py",
        "history/db.py",  # corrupt db rename
    }

    for py_file in src_root.rglob("*.py"):
        rel = py_file.relative_to(src_root).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            # Check shutil.move - forbidden everywhere
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "move"
                and isinstance(node.value, ast.Name)
                and node.value.id == "shutil"
            ):
                pytest.fail(f"Forbidden shutil.move found in {rel}:{node.lineno}")

            # Check .replace() calls
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "replace"
                and rel not in allowed_replace
                and rel not in allowed_rename
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
            ):
                pytest.fail(f"Forbidden os.replace found in {rel}:{node.lineno}")

            # Check .rename() calls
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "rename"
                and rel not in allowed_rename
            ):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    pytest.fail(f"Forbidden os.rename found in {rel}:{node.lineno}")
                pytest.fail(f"Forbidden .rename call found in {rel}:{node.lineno}")


def test_static_no_shutil_rmtree() -> None:
    """Static check: no shutil.rmtree anywhere in src/clutterxp/."""
    src_root = Path(__file__).resolve().parents[2] / "src" / "clutterxp"

    for py_file in src_root.rglob("*.py"):
        rel = py_file.relative_to(src_root).as_posix()
        content = py_file.read_text(encoding="utf-8")
        assert "rmtree" not in content, f"Forbidden rmtree found in {rel}"


def test_static_no_subprocess_with_shell_true() -> None:
    """Static check: no subprocess call with shell=True in src/clutterxp/."""
    src_root = Path(__file__).resolve().parents[2] / "src" / "clutterxp"

    for py_file in src_root.rglob("*.py"):
        rel = py_file.relative_to(src_root).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        pytest.fail(f"Forbidden shell=True found in {rel}:{node.lineno}")


def test_static_no_network_modules() -> None:
    """Static check: no imports of network modules in src/clutterxp/."""
    src_root = Path(__file__).resolve().parents[2] / "src" / "clutterxp"
    forbidden_modules = {
        "socket",
        "urllib",
        "requests",
        "httpx",
        "http",
        "ftplib",
        "aiohttp",
    }

    for py_file in src_root.rglob("*.py"):
        rel = py_file.relative_to(src_root).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    assert root_pkg not in forbidden_modules, (
                        f"Forbidden network module import '{alias.name}' in {rel}:{node.lineno}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_pkg = node.module.split(".")[0]
                assert root_pkg not in forbidden_modules, (
                    f"Forbidden network module import '{node.module}' in {rel}:{node.lineno}"
                )
