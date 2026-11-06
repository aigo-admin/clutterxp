"""Property-based integration tests using Hypothesis.

Verifies system-wide properties across modules:
1. engine.decide is deterministic for any rule set and file.
2. Planned moves NEVER escape configured destination roots.
3. Unmatched/ambiguous/ignored files NEVER result in move actions.
4. Planning is deterministic across repeated runs on the same inputs.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from clutterxp.config.runtime import RuntimeConfig
from clutterxp.config.schema import Conditions, RuleSpec, Settings
from clutterxp.models import CollisionPolicy, DecisionKind, FileInfo, PlanStatus
from clutterxp.planner import build_plan
from clutterxp.rules import build_engine

# Strategies for hypothesis data generation
_safe_names = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="_-.",
    ),
    min_size=1,
    max_size=30,
).filter(lambda s: s not in (".", "..") and not s.startswith("."))

_extensions = st.sampled_from(["pdf", "txt", "docx", "jpg", "png", "tar.gz", "csv", "zip", ""])


@st.composite
def file_info_strategy(draw: st.DrawFn) -> FileInfo:
    stem = draw(_safe_names)
    ext = draw(_extensions)
    name = f"{stem}.{ext}" if ext else stem
    size = draw(st.integers(min_value=0, max_value=10_000_000))
    mtime_ns = draw(st.integers(min_value=1_000_000_000, max_value=2_000_000_000_000_000_000))
    p = Path(f"/test/inbox/{name}")
    return FileInfo(
        path=p,
        name=name,
        stem=stem,
        ext=ext,
        size=size,
        mtime_ns=mtime_ns,
        inode=draw(st.integers(min_value=1, max_value=100000)),
        device=1,
    )


@st.composite
def rule_spec_strategy(draw: st.DrawFn) -> RuleSpec:
    rule_name = draw(_safe_names)
    priority = draw(st.integers(min_value=1, max_value=100))
    keyword = draw(_safe_names)
    dest_sub = draw(_safe_names)
    dest = f"/test/organized/{dest_sub}"
    return RuleSpec(
        name=rule_name,
        priority=priority,
        keywords=[keyword],
        destination=dest,
        conditions=Conditions(),
        collision=CollisionPolicy.RENAME,
    )


# =============================================================================
# Property 1: Decision determinism
# =============================================================================


@settings(max_examples=50, deadline=None)
@given(
    file=file_info_strategy(),
    rules=st.lists(rule_spec_strategy(), min_size=1, max_size=5),
)
def test_hypothesis_decide_is_deterministic(file: FileInfo, rules: list[RuleSpec]) -> None:
    """engine.decide produces identical Decision when called multiple times."""
    engine = build_engine(rules, Settings())
    dec1 = engine.decide(file)
    dec2 = engine.decide(file)

    assert dec1.kind == dec2.kind
    assert dec1.reason == dec2.reason
    if dec1.hit is not None:
        assert dec2.hit is not None
        assert dec1.hit.rule_name == dec2.hit.rule_name
        assert dec1.hit.dest_dir == dec2.hit.dest_dir
        assert dec1.hit.priority == dec2.hit.priority
    else:
        assert dec2.hit is None


# =============================================================================
# Property 2: Never move outside configured destinations
# =============================================================================


@settings(max_examples=50, deadline=None)
@given(
    file=file_info_strategy(),
    rules=st.lists(rule_spec_strategy(), min_size=1, max_size=5),
)
def test_hypothesis_never_moves_outside_configured_destinations(
    file: FileInfo, rules: list[RuleSpec]
) -> None:
    """When a file matches, destination path is strictly contained in configured root."""
    engine = build_engine(rules, Settings())
    dec = engine.decide(file)

    if dec.kind == DecisionKind.MATCH:
        assert dec.hit is not None
        static_root = dec.hit.static_root
        dest_dir = dec.hit.dest_dir
        # Must be equal to static root or a descendant of static root
        assert dest_dir == static_root or static_root in dest_dir.parents


# =============================================================================
# Property 3: Unmatched files are NEVER planned to move
# =============================================================================


@settings(max_examples=50, deadline=None)
@given(
    file=file_info_strategy(),
    rules=st.lists(rule_spec_strategy(), min_size=1, max_size=5),
)
def test_hypothesis_unmatched_files_never_planned_to_move(
    file: FileInfo, rules: list[RuleSpec]
) -> None:
    """Files whose decision is not MATCH are never planned with WILL_MOVE."""
    settings = Settings()
    engine = build_engine(rules, settings)
    dec = engine.decide(file)

    if dec.kind != DecisionKind.MATCH:
        runtime_cfg = RuntimeConfig(
            config_path=Path("/test/config.yaml"),
            mtime_ns=0,
            settings=settings,
            watch_entries=(),
            rules=tuple(rules),
            disabled_rules=(),
            errors=(),
            warnings=(),
        )
        plan = build_plan(iter([file]), engine, runtime_cfg, fs_check=False)
        for action in plan.actions:
            assert action.status not in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED)


# =============================================================================
# Property 4: Plan is deterministic
# =============================================================================


@settings(max_examples=30, deadline=None)
@given(
    files=st.lists(file_info_strategy(), min_size=1, max_size=8),
    rules=st.lists(rule_spec_strategy(), min_size=1, max_size=4),
)
def test_hypothesis_plan_is_deterministic(files: list[FileInfo], rules: list[RuleSpec]) -> None:
    """build_plan generates identical planned action statuses across repeated invocations."""
    settings = Settings()
    engine = build_engine(rules, settings)
    runtime_cfg = RuntimeConfig(
        config_path=Path("/test/config.yaml"),
        mtime_ns=0,
        settings=settings,
        watch_entries=(),
        rules=tuple(rules),
        disabled_rules=(),
        errors=(),
        warnings=(),
    )

    plan1 = build_plan(iter(files), engine, runtime_cfg, fs_check=False)
    plan2 = build_plan(iter(files), engine, runtime_cfg, fs_check=False)

    assert len(plan1.actions) == len(plan2.actions)
    for a1, a2 in zip(plan1.actions, plan2.actions, strict=True):
        assert a1.file.name == a2.file.name
        assert a1.status == a2.status
        assert a1.dest_path == a2.dest_path
