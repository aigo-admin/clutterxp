"""Safe file mover implementation conforming to Mover Protocol."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import logging
import os
import shutil
import stat
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any, Literal

from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    ErrorCode,
    MoveResult,
    MoveStrategy,
    ProgressCb,
)
from clutterxp.mover import fsops
from clutterxp.mover.collision import candidate_names

logger = logging.getLogger(__name__)


def _hash_file(path: Path) -> str:
    """Compute SHA-256 digest of a file in 1 MiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class SafeMover:
    """Safe mover with atomic publish, collision policies, and cross-filesystem fallback."""

    def __init__(
        self,
        *,
        verify_copy: Literal["size", "hash"] = "size",
        fs: Any = None,
    ) -> None:
        self._verify_copy = verify_copy
        self._fs = fs if fs is not None else fsops

    def move_file(
        self,
        src: Path,
        dest_dir: Path,
        *,
        new_name: str | None = None,
        collision: CollisionPolicy = CollisionPolicy.SKIP,
        create: CreateDestinations = CreateDestinations.LEAF,
        static_root: Path | None = None,
        expect_size: int | None = None,
        expect_mtime_ns: int | None = None,
        progress: ProgressCb | None = None,
    ) -> MoveResult:
        """Move a single regular file to dest_dir safely."""
        created_dirs: list[Path] = []

        try:
            # 1. Precondition: src exists and is a regular file
            try:
                st = src.lstat()
            except FileNotFoundError:
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.SRC_VANISHED,
                    message=f"Source file {src} does not exist",
                )
            except OSError as e:
                code = (
                    ErrorCode.PERMISSION
                    if e.errno in (errno.EACCES, errno.EPERM)
                    else ErrorCode.UNKNOWN
                )
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=code,
                    message=str(e),
                )

            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.SRC_NOT_REGULAR,
                    message=f"Source {src} is not a regular file",
                )

            # 2. Precondition: expect_size and expect_mtime_ns (TOCTOU re-check)
            if expect_size is not None and st.st_size != expect_size:
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.SRC_CHANGED,
                    message=f"Source size changed: expected {expect_size}, got {st.st_size}",
                )
            if expect_mtime_ns is not None and st.st_mtime_ns != expect_mtime_ns:
                msg = f"Source mtime changed: expected {expect_mtime_ns}, got {st.st_mtime_ns}"
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.SRC_CHANGED,
                    message=msg,
                )

            # Precondition: Final name validation
            dst_name = new_name if new_name is not None else src.name
            if "/" in dst_name or "\x00" in dst_name or dst_name in (".", ".."):
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.DEST_INVALID,
                    message=f"Invalid destination file name: {dst_name!r}",
                )

            # Precondition: destination directory is not source's own directory (no-op)
            try:
                if dest_dir.resolve() == src.parent.resolve() and dst_name == src.name:
                    return MoveResult(
                        ok=False,
                        src=src,
                        dst=None,
                        strategy=None,
                        code=ErrorCode.DEST_INVALID,
                        message="Destination directory is source's own directory (no-op)",
                    )
            except OSError:
                pass

            # 3. Precondition: Destination directory creation per `create` mode
            dest_res = self._prepare_dest_dir(dest_dir, create, static_root, created_dirs)
            if dest_res is not None:
                return dest_res

            # Precondition: dest_dir writable
            if not os.access(dest_dir, os.W_OK | os.X_OK):
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.DEST_NOT_WRITABLE,
                    message=f"Destination directory {dest_dir} is not writable",
                    created_dirs=tuple(created_dirs),
                )

            # Precondition: Enough space (best-effort)
            is_same_fs = self._fs.same_device(src, dest_dir)
            if not is_same_fs:
                try:
                    usage = shutil.disk_usage(dest_dir)
                    if usage.free < st.st_size:
                        return MoveResult(
                            ok=False,
                            src=src,
                            dst=None,
                            strategy=None,
                            code=ErrorCode.NO_SPACE,
                            message=f"Not enough space in {dest_dir}: {usage.free} < {st.st_size}",
                            created_dirs=tuple(created_dirs),
                        )
                except OSError:
                    pass

            # Precondition 4 & Collision handling:
            if is_same_fs:
                try:
                    return self._move_strategy_a(
                        src=src,
                        dest_dir=dest_dir,
                        dst_name=dst_name,
                        collision=collision,
                        created_dirs=created_dirs,
                    )
                except OSError as err:
                    if err.errno == errno.EXDEV:
                        # Cross-filesystem detected/simulated, switch to Strategy B
                        return self._move_strategy_b(
                            src=src,
                            dest_dir=dest_dir,
                            dst_name=dst_name,
                            collision=collision,
                            created_dirs=created_dirs,
                            progress=progress,
                        )
                    raise
            else:
                return self._move_strategy_b(
                    src=src,
                    dest_dir=dest_dir,
                    dst_name=dst_name,
                    collision=collision,
                    created_dirs=created_dirs,
                    progress=progress,
                )

        except OSError as e:
            code = ErrorCode.UNKNOWN
            if e.errno == errno.ENOSPC:
                code = ErrorCode.NO_SPACE
            elif e.errno in (errno.EACCES, errno.EPERM):
                code = ErrorCode.PERMISSION
            elif e.errno == errno.ENOENT:
                code = ErrorCode.SRC_VANISHED
            return MoveResult(
                ok=False,
                src=src,
                dst=None,
                strategy=None,
                code=code,
                message=str(e),
                created_dirs=tuple(created_dirs),
            )

    def _prepare_dest_dir(
        self,
        dest_dir: Path,
        create: CreateDestinations,
        static_root: Path | None,
        created_dirs: list[Path],
    ) -> MoveResult | None:
        """Create destination directories per policy, recording created paths deepest-last."""
        # Reject any destination directory containing a symlink component
        curr_check = dest_dir
        while curr_check != curr_check.parent:
            if curr_check.is_symlink():
                return MoveResult(
                    ok=False,
                    src=dest_dir,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.DEST_INVALID,
                    message=f"Destination component {curr_check} is a symlink",
                    created_dirs=tuple(created_dirs),
                )
            curr_check = curr_check.parent

        if create == CreateDestinations.NEVER:
            if not dest_dir.exists() or not dest_dir.is_dir():
                return MoveResult(
                    ok=False,
                    src=dest_dir,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.DEST_MISSING,
                    message=f"Destination directory {dest_dir} does not exist",
                )
            return None

        if create == CreateDestinations.LEAF:
            if dest_dir.is_dir():
                return None

            if static_root is not None:
                # static_root must exist or its parent must exist
                if not static_root.exists():
                    if static_root.parent.is_dir():
                        static_root.mkdir()
                        created_dirs.append(static_root)
                    else:
                        return MoveResult(
                            ok=False,
                            src=dest_dir,
                            dst=None,
                            strategy=None,
                            code=ErrorCode.DEST_MISSING,
                            message=f"Static root parent {static_root.parent} does not exist",
                        )

                # Subdirectories below static_root up to dest_dir are created
                try:
                    rel = dest_dir.relative_to(static_root)
                    curr = static_root
                    for part in rel.parts:
                        curr = curr / part
                        if curr.is_symlink():
                            return MoveResult(
                                ok=False,
                                src=dest_dir,
                                dst=None,
                                strategy=None,
                                code=ErrorCode.DEST_INVALID,
                                message=f"Path component {curr} is a symlink",
                                created_dirs=tuple(created_dirs),
                            )
                        if not curr.exists():
                            curr.mkdir()
                            created_dirs.append(curr)
                except ValueError:
                    # dest_dir is not relative to static_root, check leaf parent
                    if not dest_dir.parent.is_dir():
                        return MoveResult(
                            ok=False,
                            src=dest_dir,
                            dst=None,
                            strategy=None,
                            code=ErrorCode.DEST_MISSING,
                            message=f"Destination parent {dest_dir.parent} does not exist",
                        )
                    dest_dir.mkdir()
                    created_dirs.append(dest_dir)
            else:
                if not dest_dir.parent.is_dir():
                    return MoveResult(
                        ok=False,
                        src=dest_dir,
                        dst=None,
                        strategy=None,
                        code=ErrorCode.DEST_MISSING,
                        message=f"Destination parent {dest_dir.parent} does not exist",
                    )
                dest_dir.mkdir()
                created_dirs.append(dest_dir)
            return None

        if create == CreateDestinations.ALL:
            if dest_dir.is_dir():
                return None
            if dest_dir.exists() and not dest_dir.is_dir():
                return MoveResult(
                    ok=False,
                    src=dest_dir,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.DEST_MISSING,
                    message=f"Destination path {dest_dir} exists and is not a directory",
                )
            to_create: list[Path] = []
            curr = dest_dir
            while not curr.exists() and curr != curr.parent:
                if curr.is_symlink():
                    return MoveResult(
                        ok=False,
                        src=dest_dir,
                        dst=None,
                        strategy=None,
                        code=ErrorCode.DEST_INVALID,
                        message=f"Path component {curr} is a symlink",
                        created_dirs=tuple(created_dirs),
                    )
                to_create.append(curr)
                curr = curr.parent
            to_create.reverse()
            for d in to_create:
                d.mkdir()
                created_dirs.append(d)
            return None

        return None

    def _publish_same_fs(
        self,
        src: Path,
        dst: Path,
    ) -> tuple[bool, MoveStrategy | None, bool]:
        """Publish src to dst on the same filesystem.

        Returns (success, strategy, unlink_failed).
        """
        # 1. rename_noreplace
        try:
            if self._fs.rename_noreplace(src, dst):
                return True, MoveStrategy.RENAME_NOREPLACE, False
        except OSError as e:
            if e.errno == errno.EXDEV:
                raise
            # Fall through to next strategy on non-fatal errors

        # If rename_noreplace returned False, check if dst exists (collision)
        if os.path.lexists(dst):
            return False, None, False

        # 2. link_unlink
        try:
            if self._fs.link_unlink(src, dst):
                return True, MoveStrategy.LINK_UNLINK, False
            else:
                # Check if link succeeded but unlink failed!
                if os.path.lexists(dst) and os.path.lexists(src):
                    with contextlib.suppress(OSError):
                        if dst.stat().st_ino == src.stat().st_ino:
                            return False, MoveStrategy.LINK_UNLINK, True
        except OSError as e:
            if e.errno == errno.EXDEV:
                raise

        if os.path.lexists(dst):
            return False, None, False

        # 3. checked_rename
        try:
            if self._fs.checked_rename(src, dst):
                return True, MoveStrategy.CHECKED_RENAME, False
        except OSError as e:
            if e.errno == errno.EXDEV:
                raise

        return False, None, False

    def _candidates_for(
        self,
        dst_name: str,
        collision: CollisionPolicy,
    ) -> Generator[str, None, None]:
        """Generate candidate destination names based on collision policy."""
        yield dst_name
        if collision == CollisionPolicy.RENAME:
            yield from candidate_names(dst_name)

    def _move_strategy_a(
        self,
        src: Path,
        dest_dir: Path,
        dst_name: str,
        collision: CollisionPolicy,
        created_dirs: list[Path],
    ) -> MoveResult:
        """Strategy A: same filesystem move using atomic no-clobber primitives."""
        # Check initial collision for SKIP policy
        initial_dst = dest_dir / dst_name
        if collision == CollisionPolicy.SKIP and os.path.lexists(initial_dst):
            return MoveResult(
                ok=False,
                src=src,
                dst=None,
                strategy=None,
                code=ErrorCode.COLLISION,
                message=f"Destination {initial_dst} already exists",
                created_dirs=tuple(created_dirs),
            )

        for cand_name in self._candidates_for(dst_name, collision):
            cand_path = dest_dir / cand_name

            # Skip existing candidates in RENAME policy
            if os.path.lexists(cand_path):
                if collision == CollisionPolicy.SKIP:
                    return MoveResult(
                        ok=False,
                        src=src,
                        dst=None,
                        strategy=None,
                        code=ErrorCode.COLLISION,
                        message=f"Destination {cand_path} already exists",
                        created_dirs=tuple(created_dirs),
                    )
                continue

            success, strategy, unlink_failed = self._publish_same_fs(src, cand_path)

            if unlink_failed:
                # Link succeeded but unlink(src) failed: keep dst, return PERMISSION
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=cand_path,
                    strategy=MoveStrategy.LINK_UNLINK,
                    code=ErrorCode.PERMISSION,
                    message="copied but could not remove source",
                    created_dirs=tuple(created_dirs),
                    renamed_due_to_collision=(cand_path.name != dst_name),
                )

            if success:
                renamed = cand_path.name != dst_name
                return MoveResult(
                    ok=True,
                    src=src,
                    dst=cand_path,
                    strategy=strategy,
                    code=ErrorCode.NONE,
                    message="",
                    created_dirs=tuple(created_dirs),
                    renamed_due_to_collision=renamed,
                    bytes_copied=0,
                )

            # Failure: another process occupied cand_path (race collision)
            if collision == CollisionPolicy.SKIP:
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.COLLISION,
                    message=f"Destination {cand_path} already exists",
                    created_dirs=tuple(created_dirs),
                )

        return MoveResult(
            ok=False,
            src=src,
            dst=None,
            strategy=None,
            code=ErrorCode.COLLISION,
            message="Too many collisions (capped at 9999)",
            created_dirs=tuple(created_dirs),
        )

    def _move_strategy_b(
        self,
        src: Path,
        dest_dir: Path,
        dst_name: str,
        collision: CollisionPolicy,
        created_dirs: list[Path],
        progress: ProgressCb | None,
    ) -> MoveResult:
        """Strategy B: cross-filesystem copy-verify-publish-unlink move."""
        # 1. Record size0 and mtime_ns0
        st0 = src.lstat()
        size0 = st0.st_size
        mtime_ns0 = st0.st_mtime_ns
        atime_ns0 = st0.st_atime_ns
        mode0 = st0.st_mode

        # Check initial collision for SKIP policy
        initial_dst = dest_dir / dst_name
        if collision == CollisionPolicy.SKIP and os.path.lexists(initial_dst):
            return MoveResult(
                ok=False,
                src=src,
                dst=None,
                strategy=None,
                code=ErrorCode.COLLISION,
                message=f"Destination {initial_dst} already exists",
                created_dirs=tuple(created_dirs),
            )

        # 2. Create temp file in dest_dir
        temp_name = f".{dst_name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.clutterxp-partial"
        temp_path = dest_dir / temp_name
        published = False

        try:
            fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                # Copy in 1 MiB chunks
                chunk_size = 1024 * 1024
                copied = 0
                last_progress_time = 0.0

                with src.open("rb") as fsrc, open(fd, "wb", closefd=False) as fdst:
                    while True:
                        chunk = fsrc.read(chunk_size)
                        if not chunk:
                            break
                        fdst.write(chunk)
                        copied += len(chunk)
                        if progress is not None:
                            now = time.monotonic()
                            if now - last_progress_time >= 0.1 or copied >= size0:
                                progress(copied, size0)
                                last_progress_time = now

                    if size0 == 0 and progress is not None:
                        progress(0, 0)
                    fdst.flush()

                # 3. fsync temp file
                self._fs.fsync_file(temp_path)
            finally:
                os.close(fd)

            # 4. Verify
            # Re-stat src
            st_check1 = src.lstat()
            if st_check1.st_size != size0 or st_check1.st_mtime_ns != mtime_ns0:
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.SRC_CHANGED,
                    message="Source changed during copy",
                    created_dirs=tuple(created_dirs),
                )

            # Verify temp size
            st_temp = temp_path.stat()
            if st_temp.st_size != size0:
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.VERIFY_FAILED,
                    message=f"Temp file size mismatch: {st_temp.st_size} != {size0}",
                    created_dirs=tuple(created_dirs),
                )

            # Verify hash if configured
            if self._verify_copy == "hash":
                src_hash = _hash_file(src)
                temp_hash = _hash_file(temp_path)
                if src_hash != temp_hash:
                    return MoveResult(
                        ok=False,
                        src=src,
                        dst=None,
                        strategy=None,
                        code=ErrorCode.VERIFY_FAILED,
                        message="SHA-256 hash mismatch between source and destination copy",
                        created_dirs=tuple(created_dirs),
                    )

            # 5. Re-stat src again to catch any change during hashing
            st_check2 = src.lstat()
            if st_check2.st_size != size0 or st_check2.st_mtime_ns != mtime_ns0:
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.SRC_CHANGED,
                    message="Source changed during copy",
                    created_dirs=tuple(created_dirs),
                )

            # 6. Apply metadata to temp file
            temp_path.chmod(mode0)
            os.utime(temp_path, ns=(atime_ns0, mtime_ns0))
            with contextlib.suppress(OSError, PermissionError):
                shutil.copystat(src, temp_path, follow_symlinks=False)

            # 7. Publish with no-clobber primitive
            target_path: Path | None = None
            for cand_name in self._candidates_for(dst_name, collision):
                cand_path = dest_dir / cand_name
                if os.path.lexists(cand_path):
                    if collision == CollisionPolicy.SKIP:
                        return MoveResult(
                            ok=False,
                            src=src,
                            dst=None,
                            strategy=None,
                            code=ErrorCode.COLLISION,
                            message=f"Destination {cand_path} already exists",
                            created_dirs=tuple(created_dirs),
                        )
                    continue

                success, _, _ = self._publish_same_fs(temp_path, cand_path)
                if success:
                    target_path = cand_path
                    published = True
                    break

                if collision == CollisionPolicy.SKIP:
                    return MoveResult(
                        ok=False,
                        src=src,
                        dst=None,
                        strategy=None,
                        code=ErrorCode.COLLISION,
                        message=f"Destination {cand_path} already exists",
                        created_dirs=tuple(created_dirs),
                    )

            if target_path is None or not published:
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.COLLISION,
                    message="Could not publish destination file (too many collisions or race)",
                    created_dirs=tuple(created_dirs),
                )

            # 8. fsync dest_dir
            self._fs.fsync_dir(dest_dir)

            # 9. Unlink src
            try:
                src.unlink()
            except OSError as err:
                # Keep both files! Do NOT delete target_path!
                logger.warning("Unlink source '%s' failed after verified copy: %s", src, err)
                return MoveResult(
                    ok=False,
                    src=src,
                    dst=target_path,
                    strategy=MoveStrategy.COPY_VERIFY,
                    code=ErrorCode.PERMISSION,
                    message="copied but could not remove source",
                    created_dirs=tuple(created_dirs),
                    renamed_due_to_collision=(target_path.name != dst_name),
                    bytes_copied=size0,
                )

            # 10. fsync src's parent dir
            self._fs.fsync_dir(src.parent)

            renamed = target_path.name != dst_name
            return MoveResult(
                ok=True,
                src=src,
                dst=target_path,
                strategy=MoveStrategy.COPY_VERIFY,
                code=ErrorCode.NONE,
                message="",
                created_dirs=tuple(created_dirs),
                renamed_due_to_collision=renamed,
                bytes_copied=size0,
            )

        finally:
            if not published and temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()


__all__ = ["SafeMover"]
