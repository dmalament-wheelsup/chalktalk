"""The signal contract.

Five signals, and the set is closed. A signal knows how to turn its own
parameters into a predicate; it knows nothing about football. If a concept
cannot be expressed in these five, the feature layer is missing an attribute —
that is the fix, not a sixth signal (D22).

Everything a signal emits is either a quoted identifier checked against the
catalog or a bound parameter. Literal values never reach the SQL text.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

import duckdb
from pydantic import BaseModel

from chalktalk.config import Settings
from chalktalk.coverage import Coverage
from chalktalk.features.catalog import Attribute, UnknownAttribute, resolve
from chalktalk.spec_types import SELF_NS

if TYPE_CHECKING:
    from chalktalk.definitions.spec import Definition
    from chalktalk.definitions.store import DefinitionStore


@dataclass(frozen=True)
class AttrRef:
    """An attribute as a definition addresses it: namespace plus name."""

    namespace: str  # SELF_NS for the definition entity's own table
    name: str

    @property
    def ref(self) -> str:
        return self.name if self.namespace == SELF_NS else f"{self.namespace}.{self.name}"

    def __str__(self) -> str:
        return self.ref


@dataclass
class CTE:
    name: str
    sql: str
    params: list[Any] = field(default_factory=list)


@dataclass
class Compiled:
    """A predicate with its parameters, plus whatever it needs around it.

    ``predicate_sql`` carries ``{namespace}`` placeholders rather than table
    aliases, because the same definition compiles differently depending on the
    entity it is lifted into. The query compiler resolves them (phase 6).
    """

    predicate_sql: str
    params: list[Any] = field(default_factory=list)
    ctes: list[CTE] = field(default_factory=list)
    namespaces_used: set[str] = field(default_factory=set)
    terms_used: list[str] = field(default_factory=list)


@dataclass
class ValidationCtx:
    entity: str
    conn: duckdb.DuckDBPyConnection
    coverage: Coverage
    store: DefinitionStore
    settings: Settings

    def attribute(self, ref: AttrRef) -> Attribute:
        """Resolve an attribute reference, or raise UnknownAttribute with suggestions."""
        from chalktalk.entities import ENTITIES

        entity = self.entity
        if ref.namespace != SELF_NS:
            if ref.namespace not in ENTITIES[entity].namespaces:
                raise UnknownAttribute(ref.ref, sorted(ENTITIES[entity].namespaces))
        return resolve(entity, ref.ref, self.conn).attribute


@dataclass
class CompileCtx(ValidationCtx):
    #: definition namespace -> SQL alias. Raises EntityMismatch when the
    #: namespace does not survive the lift.
    alias_of: Callable[[str], str] = lambda ns: ns


class Signal(ABC):
    """One of the five ways a definition can be expressed."""

    id: ClassVar[str]
    Params: ClassVar[type[BaseModel]]

    def parse(self, d: Definition) -> BaseModel:
        from pydantic import ValidationError

        from chalktalk.definitions.spec import InvalidDefinition

        try:
            return self.Params.model_validate(d.params)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "params"
            raise InvalidDefinition(
                f"{self.id}: {where}: {first['msg']}", name=d.name, field=where
            ) from exc

    @abstractmethod
    def validate(self, d: Definition, ctx: ValidationCtx) -> None:
        """Raise InvalidDefinition if this definition cannot work."""

    @abstractmethod
    def compile(self, d: Definition, ctx: CompileCtx) -> Compiled:
        """Turn the definition into a predicate."""

    @abstractmethod
    def explain(self, d: Definition, ctx: ValidationCtx) -> str:
        """One line of English describing what this matches."""

    def requires(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]:
        """Attributes without which the definition cannot run; coverage is their intersection."""
        return []

    def prefers(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]:
        """Attributes that sharpen the definition; the gate warns when they are missing."""
        return []

    def evidence(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]:
        """Columns worth showing in the matched-row sample."""
        return self.requires(d, ctx)


_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def lift_compiled(compiled: Compiled, term: Definition, target_entity: str, basis: str) -> Compiled:
    """Rewrite a term's namespaces into the entity it is being used in (D16).

    A signal emits `{self}` and never consults `alias_of`, so the lift has to
    happen to the compiled text. Used inside a `player_game` query, a
    `prior_season` `player_season` term's `{self}` must become `{prior}` —
    otherwise the predicate reads `snap_share_mean` off the `player_game` row,
    where there is no such column.

    Both the composite signal and the query compiler need this, and they must
    agree, so it lives here rather than in either of them.
    """
    from chalktalk.entities import lift_namespace

    mapping: dict[str, str] = {}
    for namespace in compiled.namespaces_used:
        target = lift_namespace(term.entity, target_entity, namespace, basis=basis)
        mapping[namespace] = target if target is not None else SELF_NS

    if all(source == target for source, target in mapping.items()):
        return compiled

    # One pass, so renaming self->prior cannot then rename prior->something else.
    predicate = _PLACEHOLDER.sub(
        lambda m: "{" + mapping.get(m.group(1), m.group(1)) + "}", compiled.predicate_sql
    )
    return Compiled(
        predicate,
        compiled.params,
        compiled.ctes,
        set(mapping.values()),
        compiled.terms_used,
    )
