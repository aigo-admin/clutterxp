"""Tests for SQLite HashCache."""

from __future__ import annotations

import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clutterxp.duplicates.hashcache import HashCache


def test_get_put_round_trip(tmp_path: Path) -> None:
    """Putting and getting cached hashes works."""
    db_path = tmp_path / "cache.db"
    cache = HashCache(db_path)

    cache.put(1, 100, 2048, 123456789, "partial_abc", "full_xyz")
    assert cache.get(1, 100, 2048, 123456789) == ("partial_abc", "full_xyz")
    cache.close()


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    """Missing cache entries return (None, None)."""
    db_path = tmp_path / "cache.db"
    cache = HashCache(db_path)

    assert cache.get(1, 999, 100, 500) == (None, None)
    cache.close()


def test_changed_file_invalidates_cache(tmp_path: Path) -> None:
    """Changed mtime_ns, size, dev, or ino results in cache miss."""
    db_path = tmp_path / "cache.db"
    cache = HashCache(db_path)

    cache.put(1, 100, 500, 1000, "partial_1", "full_1")

    # Changed mtime_ns
    assert cache.get(1, 100, 500, 2000) == (None, None)
    # Changed size
    assert cache.get(1, 100, 600, 1000) == (None, None)
    # Changed dev
    assert cache.get(2, 100, 500, 1000) == (None, None)
    # Changed ino
    assert cache.get(1, 200, 500, 1000) == (None, None)

    cache.close()


def test_partial_then_full_update(tmp_path: Path) -> None:
    """Updating full hash preserves or updates partial hash."""
    db_path = tmp_path / "cache.db"
    cache = HashCache(db_path)

    # First store only partial hash
    cache.put(1, 100, 500, 1000, "partial_1", None)
    assert cache.get(1, 100, 500, 1000) == ("partial_1", None)

    # Then store full hash
    cache.put(1, 100, 500, 1000, "partial_1", "full_1")
    assert cache.get(1, 100, 500, 1000) == ("partial_1", "full_1")

    cache.close()


def test_prune_removes_old_entries(tmp_path: Path) -> None:
    """Pruning deletes rows older than the specified age in days."""
    db_path = tmp_path / "cache.db"
    cache = HashCache(db_path)

    cache.put(1, 100, 500, 1000, "p1", "f1")

    # Insert an entry with old hashed_at directly via connection
    old_time = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = cache._get_conn()
    conn.execute(
        """
        INSERT INTO hashes (dev, ino, size, mtime_ns, partial_hash, full_hash, hashed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 200, 500, 1000, "p_old", "f_old", old_time),
    )
    conn.commit()

    # Prune rows older than 90 days
    pruned = cache.prune(older_than_days=90)
    assert pruned == 1

    # Old row is gone
    assert cache.get(1, 200, 500, 1000) == (None, None)
    # Recent row remains
    assert cache.get(1, 100, 500, 1000) == ("p1", "f1")

    cache.close()


def test_clear_removes_all_entries(tmp_path: Path) -> None:
    """Clear empties the database."""
    db_path = tmp_path / "cache.db"
    cache = HashCache(db_path)

    cache.put(1, 100, 500, 1000, "p1", "f1")
    cache.put(1, 101, 500, 1000, "p2", "f2")

    cache.clear()
    assert cache.get(1, 100, 500, 1000) == (None, None)
    assert cache.get(1, 101, 500, 1000) == (None, None)
    cache.close()


def test_permissions_0600(tmp_path: Path) -> None:
    """Cache database file is created with 0600 permissions."""
    db_path = tmp_path / "secure_dir" / "cache.db"
    cache = HashCache(db_path)

    mode = stat.S_IMODE(db_path.stat().st_mode)
    assert mode == 0o600
    cache.close()


def test_A58_repeat_scan_no_full_rehash(tmp_path: Path) -> None:
    """A58: Repeat scan uses cached hash without full recalculation."""
    db_path = tmp_path / "cache.db"
    cache = HashCache(db_path)

    hash_compute_count = 0

    def counting_hasher(dev: int, ino: int, size: int, mtime: int) -> str:
        nonlocal hash_compute_count
        _cached_p, cached_f = cache.get(dev, ino, size, mtime)
        if cached_f is not None:
            return cached_f
        hash_compute_count += 1
        computed = f"hash_{size}_{ino}"
        cache.put(dev, ino, size, mtime, "partial", computed)
        return computed

    # First call: computes hash
    h1 = counting_hasher(1, 10, 1000, 50)
    assert hash_compute_count == 1

    # Second call for same file: reuses cached hash, zero computations
    h2 = counting_hasher(1, 10, 1000, 50)
    assert hash_compute_count == 1
    assert h1 == h2

    # Modified file (different mtime): computes hash again
    h3 = counting_hasher(1, 10, 1000, 51)
    assert hash_compute_count == 2
    assert h3 == "hash_1000_10"

    cache.close()


def test_concurrent_access(tmp_path: Path) -> None:
    """Multiple threads can read and write concurrently without sqlite errors."""
    db_path = tmp_path / "cache.db"
    cache = HashCache(db_path)

    def worker(i: int) -> None:
        cache.put(1, i, 1000 + i, 5000, f"p_{i}", f"f_{i}")
        val = cache.get(1, i, 1000 + i, 5000)
        assert val == (f"p_{i}", f"f_{i}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(worker, range(100)))

    cache.close()
