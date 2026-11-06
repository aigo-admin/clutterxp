"""Rule engine implementation for ClutterXP.

Implements RuleEngine protocol: compiles rules, tokenizes file names once,
evaluates rule criteria (extensions, conditions, keywords, all_of, exclude),
resolves priority and ambiguity, and produces RuleTraces per docs/02_RULE_ENGINE.md.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from clutterxp.config.schema import RuleSpec, Settings, WatchEntry
from clutterxp.interfaces import RuleEngine
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    Decision,
    DecisionKind,
    FileInfo,
    RuleHit,
    RuleTrace,
)
from clutterxp.rules.matcher import MatchContext, compile_keyword
from clutterxp.rules.template import _expand_env_and_tilde, expand, static_root
from clutterxp.rules.tokenizer import camel_tokens, is_hidden, raw_tokens, split_stem
from clutterxp.units import parse_duration_seconds, parse_size


@dataclass(frozen=True)
class _CompiledRule:
    spec: RuleSpec
    name: str
    enabled: bool
    priority: int
    destination_template: str
    collision: CollisionPolicy
    create: CreateDestinations
    extensions: tuple[str, ...]
    keywords_matchers: tuple[tuple[str, Callable[[MatchContext], str | None]], ...]
    all_of_matchers: tuple[tuple[str, Callable[[MatchContext], str | None]], ...]
    exclude_matchers: tuple[tuple[str, Callable[[MatchContext], str | None]], ...]
    min_size: int | None
    max_size: int | None
    older_than_s: int | None
    newer_than_s: int | None


class RuleEngineImpl(RuleEngine):
    """Implements RuleEngine Protocol."""

    def __init__(
        self,
        rules: list[RuleSpec],
        settings: Settings,
        *,
        now_ns: int | None = None,
        environ: dict[str, str] | None = None,
        folder_rules: dict[Path | str, Sequence[str] | Literal["all"]] | None = None,
        watch: list[WatchEntry] | None = None,
    ) -> None:
        """Precompile all rules at construction. Compile matchers, extension tuples, etc."""
        self.rules: list[RuleSpec] = rules
        self.settings: Settings = settings
        self.now_ns: int | None = now_ns
        self.environ: dict[str, str] | None = environ

        # Build folder rule restrictions mapping
        self._folder_rules: dict[Path, frozenset[str]] = {}
        if watch is not None:
            for entry in watch:
                if entry.rules != "all":
                    expanded_p = _expand_env_and_tilde(entry.path, environ)
                    norm_p = Path(os.path.normpath(expanded_p))
                    self._folder_rules[norm_p] = frozenset(r.casefold() for r in entry.rules)

        if folder_rules is not None:
            for folder_key, rule_names in folder_rules.items():
                if rule_names != "all":
                    expanded_p = _expand_env_and_tilde(str(folder_key), environ)
                    norm_p = Path(os.path.normpath(expanded_p))
                    self._folder_rules[norm_p] = frozenset(r.casefold() for r in rule_names)

        # Precompile rules
        self._compiled_rules: list[_CompiledRule] = []
        for r in rules:
            eff_mode = r.match if r.match is not None else settings.match
            eff_collision = r.collision if r.collision is not None else settings.collision
            eff_create = (
                r.create_destinations
                if r.create_destinations is not None
                else settings.create_destinations
            )

            # Extension filters (case-insensitive, strip leading dot)
            exts = tuple(e.casefold() for e in r.extensions)

            # Keyword matchers
            kw_matchers = tuple(
                (kw, compile_keyword(kw, eff_mode, settings.case_sensitive)) for kw in r.keywords
            )
            all_of_matchers = tuple(
                (kw, compile_keyword(kw, eff_mode, settings.case_sensitive)) for kw in r.all_of
            )
            exclude_matchers = tuple(
                (kw, compile_keyword(kw, eff_mode, settings.case_sensitive)) for kw in r.exclude
            )

            # Conditions
            min_size = (
                parse_size(r.conditions.min_size) if r.conditions.min_size is not None else None
            )
            max_size = (
                parse_size(r.conditions.max_size) if r.conditions.max_size is not None else None
            )
            older_than_s = (
                parse_duration_seconds(r.conditions.older_than)
                if r.conditions.older_than is not None
                else None
            )
            newer_than_s = (
                parse_duration_seconds(r.conditions.newer_than)
                if r.conditions.newer_than is not None
                else None
            )

            self._compiled_rules.append(
                _CompiledRule(
                    spec=r,
                    name=r.name,
                    enabled=r.enabled,
                    priority=r.priority,
                    destination_template=r.destination,
                    collision=eff_collision,
                    create=eff_create,
                    extensions=exts,
                    keywords_matchers=kw_matchers,
                    all_of_matchers=all_of_matchers,
                    exclude_matchers=exclude_matchers,
                    min_size=min_size,
                    max_size=max_size,
                    older_than_s=older_than_s,
                    newer_than_s=newer_than_s,
                )
            )

    def _build_context(self, file: FileInfo) -> MatchContext:
        """Tokenize file name once and build MatchContext."""
        stem, ext = split_stem(file.name)
        casefolded_name = file.name.casefold()
        raw_toks = raw_tokens(stem, casefold=not self.settings.case_sensitive)
        camel_toks = camel_tokens(stem, casefold=not self.settings.case_sensitive)

        return MatchContext(
            name=file.name,
            casefolded_name=casefolded_name,
            stem=stem,
            ext=ext,
            raw_toks=raw_toks,
            camel_toks=camel_toks,
            case_sensitive=self.settings.case_sensitive,
        )

    def _evaluate_rule(
        self,
        rule: _CompiledRule,
        ctx: MatchContext,
        file: FileInfo,
    ) -> tuple[bool, str, list[str]]:
        """Evaluate a single rule against context and file stat data.

        Returns (matched, matched_by, reasons).
        """
        if not rule.enabled:
            return False, "", ["rule is disabled"]

        # 1. Extensions check: file name ends with .<ext> (case-insensitive)
        if rule.extensions:
            name_to_check = ctx.name if self.settings.case_sensitive else ctx.casefolded_name
            ext_match = False
            for e in rule.extensions:
                target_suffix = "." + e if self.settings.case_sensitive else "." + e.casefold()
                if name_to_check.endswith(target_suffix):
                    ext_match = True
                    break
            if not ext_match:
                return (
                    False,
                    "",
                    [f"extension '{file.ext}' does not match {list(rule.extensions)}"],
                )

        # 2. Conditions check (cheap stat-based)
        if rule.min_size is not None and file.size < rule.min_size:
            return False, "", [f"size {file.size} < min_size {rule.min_size}"]
        if rule.max_size is not None and file.size > rule.max_size:
            return False, "", [f"size {file.size} > max_size {rule.max_size}"]

        ref_now_ns = self.now_ns if self.now_ns is not None else time.time_ns()
        age_s = (ref_now_ns - file.mtime_ns) / 1_000_000_000

        if rule.older_than_s is not None and age_s <= rule.older_than_s:
            return False, "", [f"file age {age_s:.1f}s <= older_than {rule.older_than_s}s"]
        if rule.newer_than_s is not None and age_s >= rule.newer_than_s:
            return False, "", [f"file age {age_s:.1f}s >= newer_than {rule.newer_than_s}s"]

        # 3. Exclude check: if any matches, rule does not apply
        for kw, matcher in rule.exclude_matchers:
            if matcher(ctx) is not None:
                return False, "", [f"excluded by keyword '{kw}'"]

        # 4. all_of check: every entry must match
        all_reasons: list[str] = []
        for kw, matcher in rule.all_of_matchers:
            reason = matcher(ctx)
            if reason is None:
                return False, "", [f"keyword '{kw}' not found"]
            all_reasons.append(reason)

        # 5. Keywords check: at least one must match if non-empty
        kw_reason: str | None = None
        if rule.keywords_matchers:
            for _kw, matcher in rule.keywords_matchers:
                reason = matcher(ctx)
                if reason is not None:
                    kw_reason = reason
                    break
            if kw_reason is None:
                return False, "", ["no keyword matched"]

        # Determine matched_by description
        if kw_reason is not None:
            matched_by = kw_reason
        elif all_reasons:
            matched_by = f"all_of ({', '.join(all_reasons)})"
        elif rule.extensions:
            matched_by = f"extension '{file.ext}'"
        else:
            matched_by = "conditions matched"

        return True, matched_by, [matched_by]

    def decide(self, file: FileInfo, *, folder: Path | None = None) -> Decision:
        """Per docs/02 sections 1 and 6. Per-folder rule subsets via folder."""
        # 0. Check ignored files per docs/02 section 2
        if file.is_symlink and not self.settings.follow_symlinks:
            return Decision(
                file=file,
                kind=DecisionKind.IGNORED,
                reason="symlink ignored (follow_symlinks is False)",
            )
        if not file.is_regular:
            return Decision(file=file, kind=DecisionKind.IGNORED, reason="not a regular file")
        if self.settings.skip_hidden and is_hidden(file.name):
            return Decision(
                file=file,
                kind=DecisionKind.IGNORED,
                reason="hidden file (skip_hidden is True)",
            )

        # 1. Filter rules by folder subset if applicable
        allowed_rule_names: frozenset[str] | None = None
        if folder is not None and self._folder_rules:
            norm_folder = Path(os.path.normpath(str(folder)))
            if norm_folder in self._folder_rules:
                allowed_rule_names = self._folder_rules[norm_folder]

        active_rules = self._compiled_rules
        if allowed_rule_names is not None:
            active_rules = [r for r in active_rules if r.name.casefold() in allowed_rule_names]

        # 2. Tokenize once
        ctx = self._build_context(file)

        # 3. Collect matching rules
        matching_hits: list[RuleHit] = []
        for r in active_rules:
            matched, matched_by, _ = self._evaluate_rule(r, ctx, file)
            if matched:
                dest_dir = expand(
                    r.destination_template,
                    file,
                    r.name,
                    now_ns=self.now_ns,
                    environ=self.environ,
                )
                raw_st = static_root(r.destination_template)
                expanded_st = _expand_env_and_tilde(raw_st, self.environ)
                static_root_path = Path(os.path.normpath(expanded_st))

                hit = RuleHit(
                    rule_name=r.name,
                    priority=r.priority,
                    dest_dir=dest_dir,
                    static_root=static_root_path,
                    matched_by=matched_by,
                    collision=r.collision,
                    create=r.create,
                    also_matched=(),
                )
                matching_hits.append(hit)

        if not matching_hits:
            return Decision(file=file, kind=DecisionKind.NO_MATCH, reason="no rule matched")

        # 4. Find lowest priority number (lower wins)
        min_priority = min(h.priority for h in matching_hits)
        top_matches = [h for h in matching_hits if h.priority == min_priority]
        lower_matches = [h for h in matching_hits if h.priority > min_priority]
        lower_names = tuple(h.rule_name for h in lower_matches)

        # 5. Single top match
        if len(top_matches) == 1:
            top = top_matches[0]
            hit = RuleHit(
                rule_name=top.rule_name,
                priority=top.priority,
                dest_dir=top.dest_dir,
                static_root=top.static_root,
                matched_by=top.matched_by,
                collision=top.collision,
                create=top.create,
                also_matched=lower_names,
            )
            return Decision(file=file, kind=DecisionKind.MATCH, hit=hit)

        # Multiple top matches: check if destinations match
        destinations = {h.dest_dir for h in top_matches}
        if len(destinations) == 1:
            top = top_matches[0]
            other_top_names = tuple(h.rule_name for h in top_matches[1:])
            all_also = other_top_names + lower_names
            hit = RuleHit(
                rule_name=top.rule_name,
                priority=top.priority,
                dest_dir=top.dest_dir,
                static_root=top.static_root,
                matched_by=top.matched_by,
                collision=top.collision,
                create=top.create,
                also_matched=all_also,
            )
            return Decision(file=file, kind=DecisionKind.MATCH, hit=hit)

        # Different destinations: AMBIGUOUS
        return Decision(
            file=file,
            kind=DecisionKind.AMBIGUOUS,
            candidates=tuple(top_matches),
            reason=(
                f"ambiguous: {len(top_matches)} rules matched with priority {min_priority} "
                "resolving to different destinations"
            ),
        )

    def trace(self, file: FileInfo) -> list[RuleTrace]:
        """One RuleTrace per rule (all rules, not filtered by folder)."""
        ctx = self._build_context(file)
        traces: list[RuleTrace] = []
        for r in self._compiled_rules:
            matched, _matched_by, reasons = self._evaluate_rule(r, ctx, file)
            traces.append(
                RuleTrace(
                    rule_name=r.name,
                    matched=matched,
                    priority=r.priority,
                    reasons=tuple(reasons),
                )
            )
        return traces
