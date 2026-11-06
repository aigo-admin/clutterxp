"""Tests for collision candidate generation and CollisionPolicy handling (A21)."""

from __future__ import annotations

from pathlib import Path

from clutterxp.models import CollisionPolicy, ErrorCode
from clutterxp.mover.collision import candidate_names, split_name_ext
from clutterxp.mover.safe_move import SafeMover


def test_split_name_ext_simple() -> None:
    assert split_name_ext("report.pdf") == ("report", ".pdf")
    assert split_name_ext("notes.txt") == ("notes", ".txt")


def test_split_name_ext_compound() -> None:
    assert split_name_ext("archive.tar.gz") == ("archive", ".tar.gz")
    assert split_name_ext("backup.TAR.GZ") == ("backup", ".TAR.GZ")
    assert split_name_ext("dist.tar.bz2") == ("dist", ".tar.bz2")
    assert split_name_ext("pkg.tar.xz") == ("pkg", ".tar.xz")
    assert split_name_ext("data.tar.zst") == ("data", ".tar.zst")


def test_split_name_ext_multi_dot() -> None:
    assert split_name_ext("archive.backup.2024.zip") == ("archive.backup.2024", ".zip")
    assert split_name_ext("my.report.v1.pdf") == ("my.report.v1", ".pdf")


def test_split_name_ext_no_extension() -> None:
    assert split_name_ext("Makefile") == ("Makefile", "")
    assert split_name_ext("LICENSE") == ("LICENSE", "")


def test_split_name_ext_hidden_files() -> None:
    assert split_name_ext(".bashrc") == (".bashrc", "")
    assert split_name_ext(".gitignore") == (".gitignore", "")
    assert split_name_ext(".bashrc.bak") == (".bashrc", ".bak")


def test_candidate_names_sequence() -> None:
    gen = candidate_names("report.pdf")
    assert next(gen) == "report (1).pdf"
    assert next(gen) == "report (2).pdf"
    assert next(gen) == "report (3).pdf"


def test_candidate_names_compound() -> None:
    gen = candidate_names("archive.tar.gz")
    assert next(gen) == "archive (1).tar.gz"
    assert next(gen) == "archive (2).tar.gz"


def test_candidate_names_no_extension() -> None:
    gen = candidate_names("Makefile")
    assert next(gen) == "Makefile (1)"
    assert next(gen) == "Makefile (2)"


def test_candidate_names_cap_at_9999() -> None:
    candidates = list(candidate_names("test.txt"))
    assert len(candidates) == 9999
    assert candidates[0] == "test (1).txt"
    assert candidates[-1] == "test (9999).txt"


def test_A21_collision_skip_reports_blocked(tmp_path: Path) -> None:
    """Acceptance criterion A21: skip policy reports BLOCKED/COLLISION without clobbering."""
    src = tmp_path / "incoming" / "data.csv"
    src.parent.mkdir()
    src.write_text("incoming new data")

    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    existing_dest = dest_dir / "data.csv"
    existing_dest.write_text("existing original data")

    mover = SafeMover()
    result = mover.move_file(src, dest_dir, collision=CollisionPolicy.SKIP)

    assert result.ok is False
    assert result.code == ErrorCode.COLLISION
    # Destination must NEVER be overwritten
    assert existing_dest.read_text() == "existing original data"
    # Source must remain intact
    assert src.exists()
    assert src.read_text() == "incoming new data"


def test_A21_collision_rename_produces_numbered(tmp_path: Path) -> None:
    """Acceptance criterion A21: rename policy produces numbered candidates 'name (1).ext'."""
    src1 = tmp_path / "incoming" / "doc.pdf"
    src1.parent.mkdir(parents=True)
    src1.write_text("first incoming")

    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    (dest_dir / "doc.pdf").write_text("existing original")

    mover = SafeMover()

    # Move first colliding file: should rename to doc (1).pdf
    result1 = mover.move_file(src1, dest_dir, collision=CollisionPolicy.RENAME)
    assert result1.ok is True
    assert result1.dst == dest_dir / "doc (1).pdf"
    assert result1.renamed_due_to_collision is True
    assert (dest_dir / "doc (1).pdf").read_text() == "first incoming"
    assert (dest_dir / "doc.pdf").read_text() == "existing original"
    assert not src1.exists()

    # Move second colliding file: should rename to doc (2).pdf
    src2 = tmp_path / "incoming" / "doc.pdf"
    src2.write_text("second incoming")
    result2 = mover.move_file(src2, dest_dir, collision=CollisionPolicy.RENAME)
    assert result2.ok is True
    assert result2.dst == dest_dir / "doc (2).pdf"
    assert result2.renamed_due_to_collision is True
    assert (dest_dir / "doc (2).pdf").read_text() == "second incoming"
    assert (dest_dir / "doc (1).pdf").read_text() == "first incoming"
    assert (dest_dir / "doc.pdf").read_text() == "existing original"
    assert not src2.exists()


def test_A21_collision_rename_compound_extension(tmp_path: Path) -> None:
    """Collision rename on .tar.gz preserves compound extension."""
    src = tmp_path / "archive.tar.gz"
    src.write_text("new tar")

    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    (dest_dir / "archive.tar.gz").write_text("old tar")

    mover = SafeMover()
    result = mover.move_file(src, dest_dir, collision=CollisionPolicy.RENAME)
    assert result.ok is True
    assert result.dst == dest_dir / "archive (1).tar.gz"
    assert (dest_dir / "archive.tar.gz").read_text() == "old tar"
    assert (dest_dir / "archive (1).tar.gz").read_text() == "new tar"
