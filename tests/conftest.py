"""Shared pytest fixtures for the ClutterXP test suite.

Provides isolated test environments, tree builders, tree snapshotting,
cross-filesystem roots, fake clock, and configuration text generators.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pytest
from ruamel.yaml import YAML

from tests.helpers import FakeClock


class MakeTreeFn(Protocol):
    def __call__(self, spec: dict[str, bytes | str], base: Path | None = None) -> Path: ...


class SnapshotFn(Protocol):
    def __call__(self, root: Path) -> dict[str, tuple[int, int, str]]: ...


class AssertUnchangedFn(Protocol):
    def __call__(
        self,
        root_or_before: Path | dict[str, tuple[int, int, str]],
        before_or_after: dict[str, tuple[int, int, str]],
    ) -> None: ...


class ConfigTextFn(Protocol):
    def __call__(
        self,
        rules: list[dict[str, Any]] | None = None,
        settings: dict[str, Any] | None = None,
        watch: list[dict[str, Any]] | None = None,
        *,
        version: int = 1,
        **extra: Any,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class EnvPaths:
    """Paths created for the isolated test environment."""

    config_path: Path
    state_dir: Path
    cache_dir: Path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EnvPaths:
    """Isolate CLUTTERXP environment variables and paths into tmp_path."""
    config_path = tmp_path / "config.yaml"
    state_dir = tmp_path / "state"
    cache_dir = tmp_path / "cache"

    state_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("CLUTTERXP_CONFIG", str(config_path))
    monkeypatch.setenv("CLUTTERXP_STATE_DIR", str(state_dir))
    monkeypatch.setenv("CLUTTERXP_CACHE_DIR", str(cache_dir))

    return EnvPaths(
        config_path=config_path,
        state_dir=state_dir,
        cache_dir=cache_dir,
    )


@pytest.fixture
def make_tree(tmp_path: Path) -> MakeTreeFn:
    """Build files from a dictionary spec {relpath: content} and return the root Path."""

    def _make(spec: dict[str, bytes | str], base: Path | None = None) -> Path:
        root = base if base is not None else tmp_path
        for relpath, content in spec.items():
            dest = root / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                dest.write_bytes(content.encode("utf-8"))
            else:
                dest.write_bytes(content)
        return root

    return _make


@pytest.fixture
def snapshot() -> SnapshotFn:
    """Snapshot a directory tree or single file: {relpath: (size, mtime_ns, sha256)}."""

    def _snap(root: Path) -> dict[str, tuple[int, int, str]]:
        result: dict[str, tuple[int, int, str]] = {}
        if not root.exists():
            return result
        if root.is_file():
            st = root.lstat()
            digest = hashlib.sha256(root.read_bytes()).hexdigest()
            result[root.name] = (st.st_size, st.st_mtime_ns, digest)
            return result

        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(root))
                st = p.lstat()
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                result[rel] = (st.st_size, st.st_mtime_ns, digest)
        return result

    return _snap


@pytest.fixture
def assert_unchanged(
    snapshot: SnapshotFn,
) -> AssertUnchangedFn:
    """Assert that two snapshots (or a root vs a snapshot) are identical."""

    def _assert(
        root_or_before: Path | dict[str, tuple[int, int, str]],
        before_or_after: dict[str, tuple[int, int, str]],
    ) -> None:
        if isinstance(root_or_before, Path):
            before = before_or_after
            after = snapshot(root_or_before)
        else:
            before = root_or_before
            after = before_or_after
        assert after == before, (
            f"Tree changed: {set(after) - set(before)} added, {set(before) - set(after)} removed"
        )

    return _assert


@pytest.fixture
def second_fs(tmp_path: Path) -> Generator[Path, None, None]:
    """Provide a temporary directory on a DIFFERENT st_dev than tmp_path."""
    tmp_dev = tmp_path.stat().st_dev
    candidate_base: Path | None = None

    for cand in (Path("/dev/shm"), Path("/tmp")):
        if cand.is_dir() and os.access(cand, os.W_OK):
            try:
                if cand.stat().st_dev != tmp_dev:
                    candidate_base = cand
                    break
            except OSError:
                continue

    if candidate_base is None:
        pytest.skip("No secondary filesystem on different st_dev available (/dev/shm, /tmp)")

    target = candidate_base / f"clutterxp_test_{uuid.uuid4().hex}"
    target.mkdir(parents=True, exist_ok=True)
    try:
        yield target
    finally:
        shutil.rmtree(target, ignore_errors=True)


@pytest.fixture
def fake_clock() -> FakeClock:
    """Provide an injectable monotonic clock."""
    return FakeClock()


@pytest.fixture
def config_text() -> ConfigTextFn:
    """Build YAML config strings programmatically matching the Config schema."""

    def _build(
        rules: list[dict[str, Any]] | None = None,
        settings: dict[str, Any] | None = None,
        watch: list[dict[str, Any]] | None = None,
        *,
        version: int = 1,
        **extra: Any,
    ) -> str:
        payload: dict[str, Any] = {"version": version}
        if settings is not None:
            payload["settings"] = settings
        if watch is not None:
            payload["watch"] = watch
        if rules is not None:
            payload["rules"] = rules
        payload.update(extra)

        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        stream = io.StringIO()
        yaml.dump(payload, stream)
        return stream.getvalue()

    return _build
