"""What a definition *is*.

A definition is a spec, never SQL (a rule that never bends). Storing
``{signal: "percentile", attr: "snap_share_mean", pctile: 90}`` rather than the
query it becomes is what makes it possible to validate against coverage, migrate
when the schema changes, and explain in plain English.

`DefinitionIn` is what a user supplies; `Definition` is that plus the provenance
and version the store maintains.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

Basis = Literal["current_season", "prior_season"]
Source = Literal["shipped", "user", "imported"]

#: Only `player_season` definitions have a basis; for every other entity the
#: notion is meaningless (D16).
BASIS_ENTITY = "player_season"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source = "user"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    copied_from: str | None = None
    note: str | None = None


class DefinitionIn(BaseModel):
    """The user-facing shape: everything the author decides."""

    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str] = Field(default_factory=list)
    entity: str
    basis: Basis | None = None
    signal: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not NAME_RE.match(value):
            raise ValueError(
                f"{value!r} is not a valid definition name: lowercase letters, digits and "
                "underscores, starting with a letter, 2 to 64 characters"
            )
        return value

    @field_validator("aliases")
    @classmethod
    def _valid_aliases(cls, values: list[str]) -> list[str]:
        for alias in values:
            if not NAME_RE.match(alias):
                raise ValueError(f"{alias!r} is not a valid alias")
        if len(set(values)) != len(values):
            raise ValueError("aliases repeat")
        return values

    @field_validator("entity")
    @classmethod
    def _known_entity(cls, value: str) -> str:
        from chalktalk.entities import ENTITIES

        if value not in ENTITIES:
            raise ValueError(f"unknown entity {value!r}; expected one of {', '.join(ENTITIES)}")
        return value

    def model_post_init(self, _context: Any) -> None:
        if self.basis is not None and self.entity != BASIS_ENTITY:
            raise ValueError(f"basis applies only to {BASIS_ENTITY} definitions, not {self.entity}")
        if self.name in self.aliases:
            raise ValueError(f"{self.name!r} is both the name and an alias")


class Definition(DefinitionIn):
    """A stored definition: the input plus what the store maintains."""

    schema_version: int = SCHEMA_VERSION
    provenance: Provenance = Field(default_factory=Provenance)
    version: int = 1

    @property
    def effective_basis(self) -> Basis:
        """`current_season` unless the author said otherwise."""
        return self.basis or "current_season"

    def names(self) -> list[str]:
        return [self.name, *self.aliases]


class DefinitionSummary(BaseModel):
    """What `list_definitions` shows without compiling anything."""

    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str]
    entity: str
    basis: Basis | None
    signal: str
    description: str
    source: Source
    version: int
    broken: str | None = None

    @classmethod
    def of(cls, d: Definition, broken: str | None = None) -> DefinitionSummary:
        return cls(
            name=d.name,
            aliases=d.aliases,
            entity=d.entity,
            basis=d.basis,
            signal=d.signal,
            description=d.description,
            source=d.provenance.source,
            version=d.version,
            broken=broken,
        )


class InvalidDefinition(Exception):
    """A definition cannot be validated, saved or compiled.

    Carries the structured detail the MCP error envelope needs (phase 8).
    """

    def __init__(
        self,
        message: str,
        *,
        name: str | None = None,
        field: str | None = None,
        did_you_mean: list[str] | None = None,
    ) -> None:
        self.message = message
        self.name = name
        self.field = field
        self.did_you_mean = did_you_mean or []
        super().__init__(message)

    def as_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {"error": "invalid_definition", "message": self.message}
        if self.name:
            error["definition"] = self.name
        if self.field:
            error["field"] = self.field
        if self.did_you_mean:
            error["did_you_mean"] = self.did_you_mean
        return error
