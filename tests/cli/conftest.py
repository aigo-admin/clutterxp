"""Shared test helpers and fixtures for CLI tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

SCHEMAS_DIR = Path(__file__).parent / "schemas"


def validate_json_schema(data: Any, schema_name: str) -> None:
    """Validate data against a Draft-7 schema file in tests/cli/schemas/."""
    schema_path = SCHEMAS_DIR / schema_name
    assert schema_path.exists(), f"Schema file not found: {schema_path}"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    _validate_node(data, schema, path="$")


def _validate_node(data: Any, schema: dict[str, Any], path: str) -> None:
    # 1. Type validation
    stype = schema.get("type")
    if stype is not None:
        allowed = [stype] if isinstance(stype, str) else list(stype)
        matches = False
        for t in allowed:
            if (
                (t == "object" and isinstance(data, dict))
                or (t == "array" and isinstance(data, list))
                or (t == "string" and isinstance(data, str))
                or (t == "integer" and isinstance(data, int) and not isinstance(data, bool))
                or (t == "boolean" and isinstance(data, bool))
                or (t == "null" and data is None)
            ):
                matches = True
        assert matches, f"Type mismatch at {path}: expected {allowed}, got {type(data).__name__}"

    # 2. Const validation
    if "const" in schema:
        assert data == schema["const"], (
            f"Const mismatch at {path}: expected {schema['const']!r}, got {data!r}"
        )

    # 3. Enum validation
    if "enum" in schema:
        assert data in schema["enum"], f"Enum mismatch at {path}: {data!r} not in {schema['enum']}"

    # 4. Required fields
    if "required" in schema and isinstance(data, dict):
        for req in schema["required"]:
            assert req in data, f"Missing required property '{req}' at {path}"

    # 5. Object properties
    if "properties" in schema and isinstance(data, dict):
        for prop, prop_schema in schema["properties"].items():
            if prop in data:
                _validate_node(data[prop], prop_schema, path=f"{path}.{prop}")

    # 6. Additional properties
    if schema.get("additionalProperties") is False and isinstance(data, dict):
        allowed_props = set(schema.get("properties", {}).keys())
        extra = set(data.keys()) - allowed_props
        assert not extra, f"Additional properties not allowed at {path}: {extra}"

    # 7. Array items
    if "items" in schema and isinstance(data, list):
        item_schema = schema["items"]
        for i, item in enumerate(data):
            _validate_node(item, item_schema, path=f"{path}[{i}]")


@pytest.fixture
def runner() -> CliRunner:
    """Fixture providing Typer CliRunner."""
    return CliRunner()
