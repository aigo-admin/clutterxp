# 07: Duplicate Detection

On-demand only (`clutterxp duplicates scan`). It never runs as part of sorting and never deletes or moves anything in v1.0. It is **read-only** on user files and keeps its own cache DB (`cache.db`).

## 1. Staged pipeline (each stage only runs on survivors of the previous one)

| Stage | Cost | Operation | Eliminates |
|---|---|---|---|
| 0. Enumerate | metadata only | walk folders, `lstat` each regular file, apply `min_size` and excludes | non-files, tiny/empty files (reported separately if `--include-empty`) |
| 1. Group by size | free | `dict[size] -> files`; drop groups of 1 | most files |
| 2. Hardlink check | free | same `(st_dev, st_ino)` means the same data, one disk copy | reported as kind `hardlink`, excluded from "wasted space" |
| 3. Partial hash | reads up to 128 KiB | SHA-256 of first 64 KiB + last 64 KiB + size | most size collisions |
| 4. Full hash | reads whole file | streaming SHA-256 (1 MiB chunks) | confirms exact duplicates |

Only files that survive stage 3 with an equal partial hash are fully hashed. Files with identical full hash are an **exact** group: byte-identical content (SHA-256).

## 2. Hash cache

`cache.db` table `hashes(dev, ino, size, mtime_ns, partial_hash, full_hash, hashed_at)` with primary key `(dev, ino, size, mtime_ns)`. A later scan reuses a cached hash only if all four key fields match, so a changed file is never trusted. Rows for files not seen for 90 days are pruned. Cache hits make re-scans of large trees nearly instant. The cache can be deleted at any time (`clutterxp duplicates cache --clear`).

## 3. Potential duplicates (candidates for human review, never "exact")

Computed from **metadata and names only** among files that are *not* in an exact group:

- Normalize the stem: casefold; strip trailing copy markers: ` (1)`, `(2)`, ` - copy`, ` copy`, `_copy`, `-copy`, `_1`, `-1`. Only the numeric/copy patterns in this closed list (kept in `namenorm.py` and covered by a golden test) are stripped. There is no fuzzy matching.
- Group by `(normalized_stem, ext)`. Groups of 2+ whose members have **different** content become `potential` groups with a `note` explaining the shared normalized name and the differing size/mtime.
- Metadata similarity alone is never called a duplicate: the report heading says "Potential duplicates (review manually)".

## 4. Output

Table (default), `--format json`, `--format csv`, or `--output FILE`.

JSON shape (`schema_version: 1`):

```json
{
  "schema_version": 1,
  "scanned": {"folders": ["..."], "files": 12840, "bytes": 51234567890},
  "exact": [
    {"sha256": "...", "size": 1048576, "wasted_bytes": 2097152,
     "files": [{"path": "...", "mtime_ns": 0, "inode": 0}, ...]}
  ],
  "hardlinks": [ {"files": ["...", "..."]} ],
  "potential": [ {"note": "same normalized name 'report', ext 'pdf', content differs", "files": ["..."]} ],
  "summary": {"exact_groups": 3, "wasted_bytes": 6291456, "potential_groups": 2}
}
```

Within each group, files are listed oldest first, so a human can see which is likely the original.

## 5. Options

`--recursive/--no-recursive` (default recursive), `--min-size 1B`, `--exclude PATTERN` (repeatable), `--jobs N` (default `min(4, cpu_count)`; use `1` on spinning disks), `--no-potential`, `--follow-symlinks` (default off), `--format`, `--output`. Progress bar on stderr (hidden with `--quiet`/`--json`). Ctrl-C stops cleanly and keeps the cache entries computed so far.

## 6. Non-blocking behavior

The scan runs in the calling thread for the CLI; hashing uses a `ThreadPoolExecutor` (`hashlib` releases the GIL on large buffers). The GUI runs it in a worker thread and cancels via a `threading.Event` checked between chunks. The frozen `DuplicateDetector` Protocol has `progress`; the concrete class additionally accepts an optional `cancel: threading.Event` keyword (a compatible extension of the Protocol signature, so it needs no contract change).

## 7. Future action (v1.1): `duplicates quarantine`

Move duplicates (never delete) into a user-chosen folder. Rules: always shows a preview; requires an explicit `--keep` strategy (`oldest`, `newest`, `shortest-path`, or a specific path); asks for confirmation; goes through the Mover and History so every move is undoable; exact groups only.

## 8. Test requirements

- Fixture trees: identical files in different dirs, same size different content, same name different content, hardlinks, symlinks (ignored), empty files, unreadable file (reported, scan continues), a file that changes during the scan (re-stat after hashing; changed means skipped with a warning).
- Partial-hash false-positive case: two files with equal head/tail/size but different middle must be separated by the full hash.
- Cache: second scan performs zero full reads (assert via a counting hash function); touching a file invalidates its entry.
- `namenorm` golden table for every supported copy pattern plus non-matches (`report2.pdf` is NOT normalized to `report.pdf`).
- Performance (`perf`): 10,000 small files scan under a documented budget on CI hardware.
