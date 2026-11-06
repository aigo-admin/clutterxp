"""Multi-stage duplicate detector implementation.

Implements the staged pipeline:
  0. Enumerate (metadata only)
  1. Group by size
  2. Hardlink check
  3. Partial hash (64 KiB head + 64 KiB tail + size)
  4. Full streaming hash (1 MiB chunks)
  Potential duplicates from normalized stem and ext.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import stat
import threading
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from clutterxp.models import DuplicateGroup, FileInfo, ProgressCb

from .hashcache import HashCache
from .namenorm import normalize_stem

logger = logging.getLogger(__name__)

PARTIAL_CHUNK_SIZE = 64 * 1024  # 64 KiB
FULL_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def compute_partial_hash(first_64k: bytes, last_64k: bytes, size: int) -> str:
    """Compute SHA-256 of first 64KiB + last 64KiB + size (8-byte little-endian)."""
    hasher = hashlib.sha256()
    hasher.update(first_64k)
    hasher.update(last_64k)
    hasher.update(size.to_bytes(8, "little"))
    return hasher.hexdigest()


def compute_full_hash(
    path: Path,
    size: int,
    *,
    cancel: threading.Event | None = None,
    on_chunk: Callable[[int], None] | None = None,
) -> str:
    """Stream full file in 1MiB chunks and compute SHA-256."""
    hasher = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Scan cancelled")
            chunk = fp.read(FULL_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
            if on_chunk is not None:
                on_chunk(len(chunk))
    return hasher.hexdigest()


class DuplicateDetectorImpl:
    """Implementation of the DuplicateDetector protocol."""

    def __init__(
        self,
        cache: HashCache | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> None:
        self.cache = cache
        self.cancel = cancel
        self.last_scanned_files: int = 0
        self.last_scanned_bytes: int = 0

    def scan(
        self,
        folders: Sequence[Path],
        *,
        recursive: bool = True,
        min_size: int = 1,
        exclude: Sequence[str] = (),
        jobs: int = 1,
        potential: bool = True,
        progress: ProgressCb | None = None,
    ) -> Sequence[DuplicateGroup]:
        """Scan folders for duplicate files."""
        self.last_scanned_files = 0
        self.last_scanned_bytes = 0

        # Stage 0: Enumerate
        scanned_files = self._stage0_enumerate(
            folders, recursive=recursive, min_size=min_size, exclude=exclude
        )
        self.last_scanned_files = len(scanned_files)
        self.last_scanned_bytes = sum(f.size for f in scanned_files)

        if self.cancel is not None and self.cancel.is_set():
            return []

        # Stage 1: Group by size
        size_groups = self._stage1_group_by_size(scanned_files)

        # Stage 2: Hardlink check
        surviving_after_stage2, hardlink_groups = self._stage2_hardlinks(size_groups)

        if self.cancel is not None and self.cancel.is_set():
            return hardlink_groups

        # Stage 3: Partial hash
        surviving_after_stage3 = self._stage3_partial_hash(surviving_after_stage2)

        if self.cancel is not None and self.cancel.is_set():
            return hardlink_groups

        # Stage 4: Full hash
        exact_groups = self._stage4_full_hash(surviving_after_stage3, jobs=jobs, progress=progress)

        # Potential duplicates: computed from remaining files not in exact or hardlink groups
        potential_groups: list[DuplicateGroup] = []
        if potential:
            exact_paths = {f.path for g in exact_groups for f in g.files}
            hardlink_paths = {f.path for g in hardlink_groups for f in g.files}
            claimed_paths = exact_paths | hardlink_paths

            remaining = [f for f in scanned_files if f.path not in claimed_paths]
            potential_groups = self._compute_potential_groups(remaining)

        return [*exact_groups, *hardlink_groups, *potential_groups]

    def _stage0_enumerate(
        self,
        folders: Sequence[Path],
        *,
        recursive: bool,
        min_size: int,
        exclude: Sequence[str],
    ) -> list[FileInfo]:
        """Enumerate files applying excludes, min_size, and symlink skipping."""
        collected: list[FileInfo] = []
        seen_paths: set[Path] = set()

        for folder in folders:
            if self.cancel is not None and self.cancel.is_set():
                break

            folder_path = folder.resolve() if folder.is_symlink() else folder
            if not folder_path.exists() or not folder_path.is_dir():
                logger.warning("Folder %s does not exist or is not a directory", folder)
                continue

            self._walk_folder(
                folder_path,
                recursive=recursive,
                min_size=min_size,
                exclude=exclude,
                collected=collected,
                seen_paths=seen_paths,
            )

        return collected

    def _walk_folder(
        self,
        current_dir: Path,
        *,
        recursive: bool,
        min_size: int,
        exclude: Sequence[str],
        collected: list[FileInfo],
        seen_paths: set[Path],
    ) -> None:
        """Walk directory recursively or flat, handling errors gracefully (S15)."""
        try:
            entries = list(os.scandir(current_dir))
        except OSError as err:
            logger.warning("Cannot read directory %s: %s", current_dir, err)
            return

        for entry in entries:
            if self.cancel is not None and self.cancel.is_set():
                break

            try:
                # Skip symlinks (follow_symlinks default False)
                if entry.is_symlink():
                    continue

                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        self._walk_folder(
                            Path(entry.path),
                            recursive=recursive,
                            min_size=min_size,
                            exclude=exclude,
                            collected=collected,
                            seen_paths=seen_paths,
                        )
                elif entry.is_file(follow_symlinks=False):
                    if any(fnmatch.fnmatch(entry.name, pat) for pat in exclude):
                        continue

                    st = entry.stat(follow_symlinks=False)
                    if st.st_size < min_size:
                        continue

                    p = Path(entry.path)
                    try:
                        resolved_path = p.resolve()
                    except OSError:
                        resolved_path = p

                    if resolved_path in seen_paths:
                        continue
                    seen_paths.add(resolved_path)

                    suffix = p.suffix
                    fi = FileInfo(
                        path=p,
                        name=p.name,
                        stem=p.stem,
                        ext=suffix[1:].lower() if suffix else "",
                        size=st.st_size,
                        mtime_ns=st.st_mtime_ns,
                        inode=st.st_ino,
                        device=st.st_dev,
                        is_regular=stat.S_ISREG(st.st_mode),
                        is_symlink=stat.S_ISLNK(st.st_mode),
                    )
                    collected.append(fi)
            except OSError as err:
                logger.warning("Error processing %s: %s", entry.path, err)
                continue

    def _stage1_group_by_size(self, files: list[FileInfo]) -> dict[int, list[FileInfo]]:
        """Group files by size and drop singletons."""
        by_size: dict[int, list[FileInfo]] = defaultdict(list)
        for f in files:
            by_size[f.size].append(f)
        return {size: flist for size, flist in by_size.items() if len(flist) >= 2}

    def _stage2_hardlinks(
        self, size_groups: dict[int, list[FileInfo]]
    ) -> tuple[dict[int, list[FileInfo]], list[DuplicateGroup]]:
        """Identify hardlink sets within each size group and exclude them from further steps."""
        hardlink_groups: list[DuplicateGroup] = []
        surviving_groups: dict[int, list[FileInfo]] = {}

        for size, flist in size_groups.items():
            by_ino: dict[tuple[int, int], list[FileInfo]] = defaultdict(list)
            for f in flist:
                by_ino[(f.device, f.inode)].append(f)

            remaining_in_size: list[FileInfo] = []
            for files_with_ino in by_ino.values():
                if len(files_with_ino) >= 2:
                    # Sort oldest first by mtime_ns, tie-break by path
                    sorted_files = sorted(files_with_ino, key=lambda x: (x.mtime_ns, str(x.path)))
                    hardlink_groups.append(
                        DuplicateGroup(
                            kind="hardlink",
                            files=tuple(sorted_files),
                            sha256=None,
                            wasted_bytes=0,
                            note="",
                        )
                    )
                else:
                    remaining_in_size.extend(files_with_ino)

            if len(remaining_in_size) >= 2:
                surviving_groups[size] = remaining_in_size

        return surviving_groups, hardlink_groups

    def _stage3_partial_hash(
        self, size_groups: dict[int, list[FileInfo]]
    ) -> dict[str, list[FileInfo]]:
        """Read 64KiB head + 64KiB tail + size and group by partial SHA-256."""
        by_partial: dict[str, list[FileInfo]] = defaultdict(list)

        for flist in size_groups.values():
            for f in flist:
                if self.cancel is not None and self.cancel.is_set():
                    break

                partial_hash = self._get_or_compute_partial_hash(f)
                if partial_hash is not None:
                    by_partial[partial_hash].append(f)

        return {ph: flist for ph, flist in by_partial.items() if len(flist) >= 2}

    def _get_or_compute_partial_hash(self, f: FileInfo) -> str | None:
        """Lookup or compute partial hash for a file."""
        if self.cache is not None:
            cached_partial, _ = self.cache.get(f.device, f.inode, f.size, f.mtime_ns)
            if cached_partial is not None:
                return cached_partial

        try:
            with f.path.open("rb") as fp:
                first_64k = fp.read(PARTIAL_CHUNK_SIZE)
                if f.size > PARTIAL_CHUNK_SIZE:
                    fp.seek(f.size - PARTIAL_CHUNK_SIZE)
                    last_64k = fp.read(PARTIAL_CHUNK_SIZE)
                else:
                    last_64k = b""

            ph = compute_partial_hash(first_64k, last_64k, f.size)
            if self.cache is not None:
                self.cache.put(f.device, f.inode, f.size, f.mtime_ns, ph, None)
            return ph
        except OSError as err:
            logger.warning("Could not read %s for partial hash: %s", f.path, err)
            return None

    def _stage4_full_hash(
        self,
        partial_groups: dict[str, list[FileInfo]],
        *,
        jobs: int,
        progress: ProgressCb | None,
    ) -> list[DuplicateGroup]:
        """Compute full streaming SHA-256 using ThreadPoolExecutor."""
        # Flatten all files that need full hash check
        all_candidates: list[tuple[FileInfo, str]] = []
        for ph, flist in partial_groups.items():
            for f in flist:
                all_candidates.append((f, ph))

        if not all_candidates:
            return []

        bytes_total = sum(f.size for f, _ in all_candidates)
        bytes_done = 0
        progress_lock = threading.Lock()

        def on_chunk(n: int) -> None:
            if progress is not None:
                nonlocal bytes_done
                with progress_lock:
                    bytes_done += n
                    progress(min(bytes_done, bytes_total), bytes_total)

        def hash_worker(item: tuple[FileInfo, str]) -> tuple[FileInfo, str] | None:
            f, ph = item
            if self.cancel is not None and self.cancel.is_set():
                return None

            # Check cache
            if self.cache is not None:
                cached_partial, cached_full = self.cache.get(f.device, f.inode, f.size, f.mtime_ns)
                if cached_full is not None and cached_partial == ph:
                    on_chunk(f.size)
                    return (f, cached_full)

            try:
                full_hash = compute_full_hash(f.path, f.size, cancel=self.cancel, on_chunk=on_chunk)
            except OSError as err:
                logger.warning("Could not read %s for full hash: %s", f.path, err)
                return None
            except InterruptedError:
                return None

            # Re-stat check after hashing: if file was modified or vanished, skip it
            try:
                st = f.path.lstat()
            except OSError as err:
                logger.warning("File %s vanished during scan: %s", f.path, err)
                return None

            if st.st_size != f.size or st.st_mtime_ns != f.mtime_ns:
                logger.warning("File %s changed during scan; skipping", f.path)
                return None

            # Update cache with both partial and full hash
            if self.cache is not None:
                self.cache.put(f.device, f.inode, f.size, f.mtime_ns, ph, full_hash)

            return (f, full_hash)

        results: list[tuple[FileInfo, str]] = []
        worker_count = max(1, jobs)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for res in executor.map(hash_worker, all_candidates):
                if res is not None:
                    results.append(res)

        by_full_hash: dict[str, list[FileInfo]] = defaultdict(list)
        for f, fh in results:
            by_full_hash[fh].append(f)

        exact_groups: list[DuplicateGroup] = []
        for fh, flist in by_full_hash.items():
            if len(flist) >= 2:
                # Oldest first by mtime_ns, tie-break by path
                sorted_files = sorted(flist, key=lambda x: (x.mtime_ns, str(x.path)))
                wasted = (len(sorted_files) - 1) * sorted_files[0].size
                exact_groups.append(
                    DuplicateGroup(
                        kind="exact",
                        files=tuple(sorted_files),
                        sha256=fh,
                        wasted_bytes=wasted,
                        note="",
                    )
                )

        return exact_groups

    def _compute_potential_groups(self, remaining_files: list[FileInfo]) -> list[DuplicateGroup]:
        """Group remaining files by normalized stem and extension."""
        by_norm: dict[tuple[str, str], list[FileInfo]] = defaultdict(list)
        for f in remaining_files:
            norm_stem = normalize_stem(f.stem)
            by_norm[(norm_stem, f.ext)].append(f)

        potential_groups: list[DuplicateGroup] = []
        for (norm_stem, ext), flist in by_norm.items():
            if len(flist) >= 2:
                sorted_files = sorted(flist, key=lambda x: (x.mtime_ns, str(x.path)))
                note = f"same normalized name '{norm_stem}', ext '{ext}', content differs"
                potential_groups.append(
                    DuplicateGroup(
                        kind="potential",
                        files=tuple(sorted_files),
                        sha256=None,
                        wasted_bytes=0,
                        note=note,
                    )
                )

        return potential_groups
