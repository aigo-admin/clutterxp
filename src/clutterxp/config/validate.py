"""Cross-configuration validation (docs/03_CONFIG.md section 4)."""

from __future__ import annotations

import difflib
import os
from pathlib import Path

from ..models import CreateDestinations, MatchMode
from ..paths import expand
from ..safety import is_denylisted
from .placeholders import extract_static_root
from .schema import Config, RuleSpec


def validate(config: Config, *, warn_only: bool = False) -> tuple[list[str], list[str]]:
    """Perform cross-configuration validation on a Config model.

    Checks:
    - Errors:
      1. Destination equals or is inside a watched folder (unless allow_chain is True).
      2. Watch folder or destination is in the system denylist or contains hidden component.
      3. Recursive watch of $HOME is rejected.
      4. Per-folder rules list references unknown rules.
    - Warnings:
      1. Watch folder does not exist.
      2. Two enabled rules share keyword at same priority with different destinations.
      3. Substring keyword shorter than 4 characters.
      4. Destination static root does not exist when create_destinations is 'never'.
      5. Destination on a different filesystem than a watched folder.

    Returns:
        (errors, warnings) lists of human-readable issues with YAML paths.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Expand paths and check watch folders
    expanded_watch_paths: dict[int, Path] = {}
    for w_idx, w in enumerate(config.watch):
        try:
            w_path = expand(w.path)
            expanded_watch_paths[w_idx] = w_path
        except Exception as e:
            errors.append(f"watch[{w_idx}].path: failed to expand path '{w.path}': {e}")
            continue

        # Denylist check for watch folder
        if is_denylisted(w_path):
            errors.append(f"watch[{w_idx}].path: path '{w.path}' is in the system denylist")

        # Recursive $HOME check
        try:
            home = Path(os.path.realpath(str(Path.home())))
            real_w = Path(os.path.realpath(str(w_path)))
            if w.recursive and real_w == home:
                errors.append(
                    f"watch[{w_idx}].path: recursive watch of $HOME ('{w.path}') "
                    "is refused for safety"
                )
        except RuntimeError:
            pass

        # Watch folder existence check (warning for config validate)
        if not w_path.exists():
            warnings.append(f"watch[{w_idx}].path: watched folder '{w.path}' does not exist")

    real_watch_paths: dict[int, Path] = {
        w_idx: Path(os.path.realpath(str(w_p))) for w_idx, w_p in expanded_watch_paths.items()
    }

    # 2. Check rule destinations (denylist, hidden components, loop guard)
    expanded_rule_roots: dict[int, Path] = {}
    for r_idx, r in enumerate(config.rules):
        static_root_str = extract_static_root(r.destination)
        try:
            dest_root = expand(static_root_str)
            expanded_rule_roots[r_idx] = dest_root
        except Exception as e:
            errors.append(
                f"rules[{r_idx}].destination: failed to expand destination '{r.destination}': {e}"
            )
            continue

        # System denylist check
        if is_denylisted(dest_root):
            errors.append(
                f"rules[{r_idx}].destination: destination '{r.destination}' "
                "is in the system denylist"
            )

        # Hidden component check in static root path
        for part in dest_root.parts:
            if part in ("/", "\\"):
                continue
            if part.startswith("."):
                errors.append(
                    f"rules[{r_idx}].destination: destination '{r.destination}' "
                    f"contains hidden component '{part}'"
                )
                break

        real_dest = Path(os.path.realpath(str(dest_root)))
        for part in real_dest.parts:
            if part in ("/", "\\"):
                continue
            if part.startswith("."):
                errors.append(
                    f"rules[{r_idx}].destination: destination '{r.destination}' "
                    f"resolves to hidden component '{part}' in '{real_dest}'"
                )
                break

        # Infinite loop guard: destination equals or is inside a watched folder
        if not config.settings.allow_chain:
            for w_idx, w_real in real_watch_paths.items():
                if dest_real_in_watch(dest_real=real_dest, watch_real=w_real):
                    errors.append(
                        f"rules[{r_idx}].destination: destination '{r.destination}' is inside "
                        f"watched folder '{config.watch[w_idx].path}' "
                        "(settings.allow_chain is false)"
                    )

    # 3. Per-folder rules list references unknown rules
    known_rule_names = {r.name for r in config.rules}
    for w_idx, w in enumerate(config.watch):
        if isinstance(w.rules, list):
            for rname in w.rules:
                if rname not in known_rule_names:
                    close = difflib.get_close_matches(rname, sorted(known_rule_names))
                    hint = f"; did you mean '{close[0]}'?" if close else ""
                    errors.append(f"watch[{w_idx}].rules: unknown rule '{rname}'{hint}")

    # 4. Overlapping keywords at same priority with different destinations
    by_priority: dict[int, list[RuleSpec]] = {}
    for r in config.rules:
        if r.enabled:
            by_priority.setdefault(r.priority, []).append(r)

    for priority_rules in by_priority.values():
        if len(priority_rules) < 2:
            continue
        for i in range(len(priority_rules)):
            for j in range(i + 1, len(priority_rules)):
                r1 = priority_rules[i]
                r2 = priority_rules[j]
                if r1.destination != r2.destination:
                    kws1 = {
                        k.casefold() for k in r1.keywords if not k.startswith(("glob:", "regex:"))
                    }
                    kws2 = {
                        k.casefold() for k in r2.keywords if not k.startswith(("glob:", "regex:"))
                    }
                    shared = kws1 & kws2
                    for kw in sorted(shared):
                        warnings.append(
                            f"rules: enabled rules '{r1.name}' and '{r2.name}' "
                            f"share keyword '{kw}' at priority {r1.priority} "
                            f"with different destinations "
                            f"('{r1.destination}' vs '{r2.destination}')"
                        )

    # 5. Substring keyword < 4 chars
    for r_idx, r in enumerate(config.rules):
        rule_mode = r.match or config.settings.match
        if rule_mode == MatchMode.SUBSTRING:
            for kw in r.keywords:
                if not kw.startswith(("glob:", "regex:")) and len(kw) < 4:
                    warnings.append(
                        f"rules[{r_idx}].keywords: substring keyword '{kw}' is shorter than "
                        "4 characters (risk of false positives)"
                    )

    # 6. Destination static root missing (when create_destinations is 'never')
    for r_idx, r in enumerate(config.rules):
        create_policy = r.create_destinations or config.settings.create_destinations
        if create_policy == CreateDestinations.NEVER:
            dest_p = expanded_rule_roots.get(r_idx)
            if dest_p is not None and not dest_p.exists():
                warnings.append(
                    f"rules[{r_idx}].destination: static root '{dest_p}' does not exist "
                    "and create_destinations is 'never'"
                )

    # 7. Cross-filesystem warning
    seen_cross_fs: set[tuple[str, str]] = set()
    for w_idx, w_path in expanded_watch_paths.items():
        if not w_path.exists():
            continue
        try:
            w_dev = w_path.stat().st_dev
        except OSError:
            continue

        for r_idx, r in enumerate(config.rules):
            if not r.enabled:
                continue
            dest_p = expanded_rule_roots.get(r_idx)
            if dest_p is None:
                continue
            # Traverse parents to find existing ancestor
            check_p = dest_p
            while not check_p.exists() and check_p.parent != check_p:
                check_p = check_p.parent
            if check_p.exists():
                try:
                    if check_p.stat().st_dev != w_dev:
                        pair = (str(w_path), str(dest_p))
                        if pair not in seen_cross_fs:
                            seen_cross_fs.add(pair)
                            warnings.append(
                                f"rules[{r_idx}].destination: destination '{r.destination}' "
                                f"is on a different filesystem than watched folder "
                                f"'{config.watch[w_idx].path}' (cross-filesystem moves will copy)"
                            )
                except OSError:
                    pass

    if warn_only:
        return ([], errors + warnings)
    return (errors, warnings)


def dest_real_in_watch(*, dest_real: Path, watch_real: Path) -> bool:
    """Return True if dest_real equals watch_real or is inside watch_real."""
    return dest_real == watch_real or watch_real in dest_real.parents
