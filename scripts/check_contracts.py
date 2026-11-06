"""Check for drift against frozen contracts.

Verifies SHA-256 of frozen files:
  - src/clutterxp/models.py
  - src/clutterxp/interfaces.py
  - src/clutterxp/units.py
and verifies the AST field signature of:
  - src/clutterxp/config/schema.py (class names + field names + type annotations)

Usage:
  python scripts/check_contracts.py          # check against lock file
  python scripts/check_contracts.py --update  # update lock file
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOCK_PATH = Path(__file__).resolve().parent / "contracts.lock.json"

FROZEN_FILES = (
    "src/clutterxp/models.py",
    "src/clutterxp/interfaces.py",
    "src/clutterxp/units.py",
)
SCHEMA_FILE = "src/clutterxp/config/schema.py"


def compute_file_sha256(path: Path) -> str:
    """Compute sha256 hex digest of file contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_schema_field_signature(schema_path: Path) -> dict[str, dict[str, str]]:
    """Extract class names, field names and annotations from schema.py using AST."""
    tree = ast.parse(schema_path.read_text(encoding="utf-8"))
    signature: dict[str, dict[str, str]] = {}

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            fields: dict[str, str] = {}
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields[item.target.id] = ast.unparse(item.annotation)
            if fields:
                signature[node.name] = dict(sorted(fields.items()))

    return dict(sorted(signature.items()))


def compute_signature_sha256(signature: dict[str, dict[str, str]]) -> str:
    """Compute sha256 hex digest of serialized AST signature."""
    canonical_json = json.dumps(signature, sort_keys=True, indent=2)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def collect_current_state(root: Path) -> dict[str, Any]:
    """Collect current sha256 hashes and schema field signature."""
    state: dict[str, Any] = {}
    for rel_path in FROZEN_FILES:
        full_path = root / rel_path
        if not full_path.exists():
            raise FileNotFoundError(f"Frozen contract file missing: {full_path}")
        state[rel_path] = compute_file_sha256(full_path)

    schema_full_path = root / SCHEMA_FILE
    if not schema_full_path.exists():
        raise FileNotFoundError(f"Schema contract file missing: {schema_full_path}")

    field_sig = extract_schema_field_signature(schema_full_path)
    state["schema_field_signature_sha256"] = compute_signature_sha256(field_sig)
    state["schema_field_signature"] = field_sig

    return state


def check_contracts(root: Path, lock_path: Path) -> list[str]:
    """Compare current state with lock file. Returns list of error messages."""
    if not lock_path.exists():
        return [f"Lock file not found at {lock_path}. Run with --update to generate it."]

    try:
        lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        return [f"Failed to parse lock file {lock_path}: {err}"]

    current = collect_current_state(root)
    errors: list[str] = []

    for rel_path in FROZEN_FILES:
        expected = lock_data.get(rel_path)
        actual = current.get(rel_path)
        if expected is None:
            errors.append(f"Missing lock entry for frozen file {rel_path}")
        elif expected != actual:
            errors.append(
                f"Contract drift in {rel_path}!\n"
                f"  Expected SHA-256: {expected}\n"
                f"  Actual SHA-256:   {actual}"
            )

    expected_sig_hash = lock_data.get("schema_field_signature_sha256")
    actual_sig_hash = current.get("schema_field_signature_sha256")
    if expected_sig_hash is None:
        errors.append("Missing lock entry for schema field signature")
    elif expected_sig_hash != actual_sig_hash:
        expected_sig = lock_data.get("schema_field_signature", {})
        actual_sig = current.get("schema_field_signature", {})
        errors.append(
            f"Schema field signature drift in {SCHEMA_FILE}!\n"
            f"  Expected signature hash: {expected_sig_hash}\n"
            f"  Actual signature hash:   {actual_sig_hash}\n"
            f"  Expected signature: {json.dumps(expected_sig, indent=2)}\n"
            f"  Actual signature:   {json.dumps(actual_sig, indent=2)}"
        )

    return errors


def update_lock(root: Path, lock_path: Path) -> None:
    """Generate and write lock file with current state."""
    state = collect_current_state(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(f"Updated contract lock file at {lock_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check for drift against frozen contracts.")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Regenerate the contracts lock file",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=DEFAULT_LOCK_PATH,
        help="Path to contracts lock file (default: scripts/contracts.lock.json)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT_DIR,
        help="Project root directory",
    )
    args = parser.parse_args(argv)

    if args.update:
        update_lock(args.root, args.lock_file)
        return 0

    errors = check_contracts(args.root, args.lock_file)
    if errors:
        print("ERROR: Contract drift detected!", file=sys.stderr)
        for err in errors:
            print(f"- {err}", file=sys.stderr)
        return 1

    print("Contract check passed: no drift detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
