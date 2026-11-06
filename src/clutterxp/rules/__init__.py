"""Rule engine: tokenization, matching, template expansion, and decision making."""

from __future__ import annotations

from typing import Any

from clutterxp.config.schema import RuleSpec, Settings
from clutterxp.rules.engine import RuleEngineImpl


def build_engine(rules: list[RuleSpec], settings: Settings, **kwargs: Any) -> RuleEngineImpl:
    """Factory. A01's RuntimeConfig will be adapted here when merged."""
    return RuleEngineImpl(rules, settings, **kwargs)


__all__ = ["RuleEngineImpl", "build_engine"]
