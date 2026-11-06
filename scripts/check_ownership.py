"""Check git diff against ownership boundaries defined in AGENTS.md.

Usage:
  python scripts/check_ownership.py --agent a01 --branch wave1/a01-config
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

OWNERSHIP: dict[str, list[str]] = {
    "a00": [
        "tests/conftest.py",
        "tests/helpers.py",
        "tests/test_harness.py",
        "scripts/",
        "src/clutterxp/py.typed",
        "src/clutterxp/rules/__init__.py",
        "src/clutterxp/mover/__init__.py",
        "src/clutterxp/history/__init__.py",
        "src/clutterxp/watch/__init__.py",
        "src/clutterxp/duplicates/__init__.py",
        "src/clutterxp/cli/__init__.py",
        "src/clutterxp/gui/__init__.py",
        "tests/__init__.py",
        "tests/rules/__init__.py",
        "tests/config/__init__.py",
        "tests/mover/__init__.py",
        "tests/history/__init__.py",
        "tests/organizer/__init__.py",
        "tests/watch/__init__.py",
        "tests/duplicates/__init__.py",
        "tests/cli/__init__.py",
        "tests/cli/schemas/__init__.py",
        "tests/safety/__init__.py",
        "tests/perf/__init__.py",
        "tests/integration/__init__.py",
    ],
    "a01": [
        "src/clutterxp/config/",  # except frozen field names
        "src/clutterxp/paths.py",
        "src/clutterxp/safety.py",
        "src/clutterxp/logging_setup.py",
        "tests/config/",
        "tests/safety/test_safety_module.py",
    ],
    "a02": [
        "src/clutterxp/rules/",
        "tests/rules/",
    ],
    "a03": [
        "src/clutterxp/mover/",
        "src/clutterxp/locking.py",
        "tests/mover/",
    ],
    "a04": [
        "src/clutterxp/history/__init__.py",
        "src/clutterxp/history/db.py",
        "src/clutterxp/history/service.py",
        "src/clutterxp/history/pathcodec.py",
        "src/clutterxp/history/schema.sql",
        "tests/history/",
    ],
    "a05": [
        "src/clutterxp/duplicates/",
        "tests/duplicates/",
    ],
    "a06": [
        "src/clutterxp/scanner.py",
        "src/clutterxp/planner.py",
        "src/clutterxp/organizer.py",
        "tests/organizer/",
    ],
    "a07": [
        "src/clutterxp/history/undo.py",
        "src/clutterxp/history/recovery.py",
        "tests/history/test_undo",
        "tests/history/test_recovery",
    ],
    "a08": [
        "src/clutterxp/watch/",
        "tests/watch/",
    ],
    "a09": [
        "src/clutterxp/cli/",
        "tests/cli/",
    ],
    "a10": [
        "pyproject.toml",
        "packaging/",
        ".github/",
        "src/clutterxp/service_install.py",
        "README.md",
        "CHANGELOG.md",
        "tests/test_service_install.py",
        "scripts/smoke_readme.sh",
    ],
    "a11": [
        "tests/integration/",
        "tests/safety/test_invariants",
        "tests/perf/",
        "scripts/e2e",
        "docs/reviews/acceptance_matrix.md",
        "docs/reviews/perf_report.md",
    ],
    "a12": [
        "docs/reviews/security_review.md",
    ],
    "a13": [
        "src/clutterxp/gui/",
        "tests/gui/",
    ],
}

ROOT_DIR = Path(__file__).resolve().parent.parent


def is_file_owned(rel_path: str, agent_id: str) -> bool:
    """Check if rel_path is owned by agent_id."""
    # Shared request protocol files can be created by any agent
    if rel_path.startswith("requests/"):
        return True

    # a00 owns empty package/subpackage __init__.py files
    if (
        agent_id == "a00"
        and rel_path.endswith("__init__.py")
        and (rel_path.startswith("src/clutterxp/") or rel_path.startswith("tests/"))
    ):
        return True

    patterns = OWNERSHIP.get(agent_id, [])
    for pat in patterns:
        if pat.endswith("/"):
            if rel_path.startswith(pat):
                return True
        elif rel_path == pat or rel_path.startswith(pat + "/") or rel_path.startswith(pat):
            return True
    return False


def get_current_branch(root: Path) -> str:
    """Get the name of the currently checked out git branch."""
    cmd = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=root, check=True)  # noqa: S603
    return res.stdout.strip()


def get_diff_files(base: str, branch: str, root: Path) -> list[str]:
    """Get list of files changed between base and branch."""
    cmd = ["git", "diff", "--name-only", f"{base}...{branch}"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=root)  # noqa: S603
    if res.returncode != 0:
        # Fallback to direct comparison if merge-base syntax fails
        fallback_cmd = ["git", "diff", "--name-only", base, branch]
        res = subprocess.run(fallback_cmd, capture_output=True, text=True, cwd=root)  # noqa: S603
        if res.returncode != 0:
            msg = f"Failed to get git diff between {base} and {branch}:\n{res.stderr}"
            raise RuntimeError(msg)

    files = [line.strip() for line in res.stdout.splitlines() if line.strip()]

    # If checking the current branch, also include uncommitted staged/unstaged changes
    current = get_current_branch(root)
    if branch in (current, "HEAD"):
        uncommitted_cmd = ["git", "diff", "--name-only", "HEAD"]
        uncommitted_res = subprocess.run(  # noqa: S603
            uncommitted_cmd, capture_output=True, text=True, cwd=root
        )
        if uncommitted_res.returncode == 0:
            for line in uncommitted_res.stdout.splitlines():
                f = line.strip()
                if f and f not in files:
                    files.append(f)

        untracked_cmd = ["git", "ls-files", "--others", "--exclude-standard"]
        untracked_res = subprocess.run(  # noqa: S603
            untracked_cmd, capture_output=True, text=True, cwd=root
        )
        if untracked_res.returncode == 0:
            for line in untracked_res.stdout.splitlines():
                f = line.strip()
                if f and f not in files:
                    files.append(f)

    return sorted(files)


def check_ownership(
    agent_id: str,
    branch: str,
    base: str = "main",
    root: Path = ROOT_DIR,
) -> list[str]:
    """Verify all files changed in branch are owned by agent_id."""
    normalized_agent = agent_id.lower().strip()
    if normalized_agent not in OWNERSHIP:
        valid_agents = ", ".join(sorted(OWNERSHIP.keys()))
        raise ValueError(f"Unknown agent '{agent_id}'. Valid agents: {valid_agents}")

    diff_files = get_diff_files(base, branch, root)
    violations: list[str] = []
    for f in diff_files:
        if not is_file_owned(f, normalized_agent):
            violations.append(f)

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check git diff against agent ownership table.")
    parser.add_argument(
        "--agent",
        required=True,
        help="Agent identifier (e.g. a00, a01, ...)",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Branch to compare against base (default: current branch)",
    )
    parser.add_argument(
        "--base",
        default="main",
        help="Base branch to compare against (default: main)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT_DIR,
        help="Project root directory",
    )
    args = parser.parse_args(argv)

    branch = args.branch if args.branch is not None else get_current_branch(args.root)
    agent = args.agent.lower().strip()

    try:
        violations = check_ownership(agent, branch, base=args.base, root=args.root)
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2

    if violations:
        print(
            f"ERROR: Ownership check failed for agent '{agent}' on branch '{branch}'!",
            file=sys.stderr,
        )
        print(f"The following {len(violations)} file(s) are outside owned paths:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print(f"Ownership check passed for agent '{agent}' on branch '{branch}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
