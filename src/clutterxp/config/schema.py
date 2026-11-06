"""Pydantic schema for config.yaml (field names and types are FROZEN).

Agents may ADD validators and error messages here, but must not rename, remove or retype a
field without a CONTRACT_CHANGE request. Cross-config checks (loops, denylist, overlaps)
live in config/validate.py, not here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import CollisionPolicy, CreateDestinations, MatchMode
from ..units import parse_duration_seconds, parse_size


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HistorySettings(_Base):
    retention_days: int = Field(365, ge=0)
    log_no_match: bool = False


class Settings(_Base):
    collision: CollisionPolicy = CollisionPolicy.SKIP
    create_destinations: CreateDestinations = CreateDestinations.LEAF
    match: MatchMode = MatchMode.TOKEN
    case_sensitive: bool = False
    skip_hidden: bool = True
    follow_symlinks: bool = False
    confirm_threshold: int = Field(10, ge=0)
    settle_seconds: float = Field(3.0, ge=0, le=3600)
    verify_copy: Literal["size", "hash"] = "size"
    record_hash: bool = False
    history: HistorySettings = Field(default_factory=HistorySettings)
    ignore: list[str] = Field(default_factory=list)
    allow_chain: bool = False

    @field_validator("match")
    @classmethod
    def _plain_modes_only(cls, v: MatchMode) -> MatchMode:
        if v in (MatchMode.GLOB, MatchMode.REGEX):
            raise ValueError("use a 'glob:' or 'regex:' prefix on a keyword instead of this mode")
        return v


class WatchEntry(_Base):
    path: str = Field(min_length=1)
    recursive: bool = False
    settle_seconds: float | None = Field(None, ge=0, le=3600)
    ignore: list[str] = Field(default_factory=list)
    rules: Literal["all"] | list[str] = "all"
    initial_scan: bool = False


class Conditions(_Base):
    min_size: int | str | None = None
    max_size: int | str | None = None
    older_than: int | str | None = None
    newer_than: int | str | None = None

    @field_validator("min_size", "max_size")
    @classmethod
    def _size(cls, v: int | str | None) -> int | str | None:
        if v is not None:
            parse_size(v)  # raises ValueError -> reported by pydantic
        return v

    @field_validator("older_than", "newer_than")
    @classmethod
    def _duration(cls, v: int | str | None) -> int | str | None:
        if v is not None:
            parse_duration_seconds(v)
        return v

    @model_validator(mode="after")
    def _ranges(self) -> "Conditions":
        if self.min_size is not None and self.max_size is not None:
            if parse_size(self.min_size) > parse_size(self.max_size):
                raise ValueError("min_size is larger than max_size")
        if self.older_than is not None and self.newer_than is not None:
            if parse_duration_seconds(self.older_than) >= parse_duration_seconds(self.newer_than):
                raise ValueError("older_than must be less than newer_than, or no file can match")
        return self

    def any_set(self) -> bool:
        return any(
            v is not None for v in (self.min_size, self.max_size, self.older_than, self.newer_than)
        )


class RuleSpec(_Base):
    name: str = Field(min_length=1)
    enabled: bool = True
    priority: int = Field(100, ge=0, le=100_000)
    destination: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    all_of: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    match: MatchMode | None = None  # None means "use settings.match"
    extensions: list[str] = Field(default_factory=list)
    conditions: Conditions = Field(default_factory=Conditions)
    collision: CollisionPolicy | None = None
    create_destinations: CreateDestinations | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if "/" in v or "\x00" in v:
            raise ValueError("rule name must not contain '/' or NUL")
        return v

    @field_validator("keywords", "all_of", "exclude")
    @classmethod
    def _non_empty_strings(cls, v: list[str]) -> list[str]:
        out = [s.strip() for s in v]
        if any(not s for s in out):
            raise ValueError("empty keyword")
        return out

    @field_validator("match")
    @classmethod
    def _plain_mode(cls, v: MatchMode | None) -> MatchMode | None:
        if v in (MatchMode.GLOB, MatchMode.REGEX):
            raise ValueError("use a 'glob:' or 'regex:' prefix on a keyword instead of this mode")
        return v

    @field_validator("extensions")
    @classmethod
    def _extensions(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for e in v:
            e = e.strip().lstrip(".").lower()
            if not e or "/" in e:
                raise ValueError(f"invalid extension {e!r}")
            out.append(e)
        return out

    @model_validator(mode="after")
    def _needs_positive_criterion(self) -> "RuleSpec":
        if not (self.keywords or self.all_of or self.extensions or self.conditions.any_set()):
            raise ValueError(
                "rule needs at least one of: keywords, all_of, extensions, conditions "
                "(a rule with only 'exclude' would match every file)"
            )
        return self


class Config(_Base):
    version: Literal[1]
    settings: Settings = Field(default_factory=Settings)
    watch: list[WatchEntry] = Field(default_factory=list)
    rules: list[RuleSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_rule_names(self) -> "Config":
        seen: dict[str, str] = {}
        for r in self.rules:
            key = r.name.casefold()
            if key in seen:
                raise ValueError(f"duplicate rule name {r.name!r} (also {seen[key]!r})")
            seen[key] = r.name
        return self
