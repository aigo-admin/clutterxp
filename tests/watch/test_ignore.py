"""Unit tests for IgnoreMatcher."""

from __future__ import annotations

from pathlib import Path

from clutterxp.watch.ignore import IgnoreMatcher


def test_builtin_ignore_patterns() -> None:
    matcher = IgnoreMatcher()

    builtin_examples = [
        "archive.zip.crdownload",
        "video.mp4.part",
        "movie.mkv.partial",
        "data.tmp",
        "scratch.temp",
        "setup.exe.download",
        "package.opdownload",
        "torrent.iso.!qB",
        "file.aria2",
        ".file.swp",
        ".file.swx",
        "document.docx~",
        "~$presentation.pptx",
        ".~lock.spreadsheet.ods#",
    ]

    for filename in builtin_examples:
        path = Path(f"/home/user/Downloads/{filename}")
        assert matcher.matches(path), f"Expected {filename} to be ignored"


def test_clutterxp_partial_always_ignored() -> None:
    # Even if builtin patterns were empty, clutterxp-partial is ignored
    matcher = IgnoreMatcher(builtin=())
    assert matcher.matches(Path("/dest/file.pdf.clutterxp-partial"))
    assert matcher.matches(Path("transfer.clutterxp-partial"))


def test_case_insensitivity() -> None:
    matcher = IgnoreMatcher()

    assert matcher.matches(Path("/home/user/Downloads/FILE.CRDOWNLOAD"))
    assert matcher.matches(Path("/home/user/Downloads/FILE.PART"))
    assert matcher.matches(Path("/home/user/Downloads/FILE.TMP"))


def test_skip_hidden_files() -> None:
    matcher_skip = IgnoreMatcher(skip_hidden=True)
    matcher_keep = IgnoreMatcher(skip_hidden=False)

    dotfile = Path("/home/user/Downloads/.DS_Store")
    file_in_dotdir = Path("/home/user/Downloads/.cache/file.pdf")
    normal_file = Path("/home/user/Downloads/file.pdf")

    assert matcher_skip.matches(dotfile)
    assert matcher_skip.matches(file_in_dotdir)
    assert not matcher_skip.matches(normal_file)

    assert not matcher_keep.matches(dotfile)
    assert not matcher_keep.matches(file_in_dotdir)
    assert not matcher_keep.matches(normal_file)


def test_extra_patterns_global_and_folder() -> None:
    matcher = IgnoreMatcher(
        global_extra=["*.bak", "*.log", "sub/*.tmp"],
        folder_extra=["special_ignore_*"],
    )

    assert matcher.matches(Path("/home/user/Downloads/test.bak"))
    assert matcher.matches(Path("/home/user/Downloads/app.log"))
    assert matcher.matches(Path("/home/user/Downloads/sub/data.tmp"))
    assert matcher.matches(Path("/home/user/Downloads/special_ignore_123.dat"))

    assert not matcher.matches(Path("/home/user/Downloads/important.pdf"))


def test_ignore_empty_path() -> None:
    matcher = IgnoreMatcher()
    assert matcher.matches(Path(""))
