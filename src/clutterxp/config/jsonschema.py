"""JSON Schema generator for clutterxp configuration."""

from __future__ import annotations

from typing import Any

from .schema import Config


def config_json_schema() -> dict[str, Any]:
    """Generate and return JSON schema dictionary for Config."""
    return Config.model_json_schema()
