"""The structured query plan.

The server has no model in it (D8). A calling model parses the user's question
into this shape; fuzzy concepts can only appear as `{"term": …}` and thresholds
as attribute rules. That is what makes the gate a lookup rather than an NLP
problem — and what makes it impossible for the server to quietly decide what
"star player" means.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chalktalk.definitions.signals.rule import Op
from chalktalk.definitions.spec import Basis

GAME_TYPES = ("REG", "WC", "DIV", "CON", "SB")
ANY_GAME_TYPE = "*"

MetricFn = Literal["count", "count_distinct", "sum", "avg", "min", "max"]


class SeasonSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    first: int = Field(alias="from")
    last: int = Field(alias="to")

    @model_validator(mode="after")
    def _ordered(self) -> SeasonSpan:
        if self.last < self.first:
            raise ValueError(f"seasons run backwards: {self.first} to {self.last}")
        return self


class TermRef(BaseModel):
    """A fuzzy word, which must resolve to a saved definition or the gate refuses."""

    model_config = ConfigDict(extra="forbid")

    term: str
    basis: Basis | None = None


class AttrRule(BaseModel):
    """A threshold the caller stated explicitly, so nothing is being inferred."""

    model_config = ConfigDict(extra="forbid")

    attr: str
    op: Op
    value: Any = None


class Not(BaseModel):
    model_config = ConfigDict(extra="forbid")
    not_: Clause = Field(alias="not")


class AnyOf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    any_of: Annotated[list[Clause], Field(min_length=1)]


class AllOf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all_of: Annotated[list[Clause], Field(min_length=1)]


Clause = TermRef | AttrRule | Not | AnyOf | AllOf

#: Which key identifies which clause. JSON Schema for this union is opaque, so
#: the tool description spells the shapes out instead (phase 8).
_CLAUSE_BY_KEY = {
    "term": TermRef,
    "attr": AttrRule,
    "not": Not,
    "any_of": AnyOf,
    "all_of": AllOf,
}


def parse_clause(value: Any) -> Clause:
    if isinstance(value, TermRef | AttrRule | Not | AnyOf | AllOf):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"a clause must be an object, got {type(value).__name__}")
    for key, model in _CLAUSE_BY_KEY.items():
        if key in value:
            return model.model_validate(value)
    raise ValueError(
        f"a clause needs one of {', '.join(_CLAUSE_BY_KEY)}; got {', '.join(value) or 'nothing'}"
    )


class TermKey(BaseModel):
    """Grouping by whether a term holds, rather than by a column."""

    model_config = ConfigDict(extra="forbid")

    term: str
    basis: Basis | None = None


GroupKey = str | TermKey


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    fn: MetricFn = "count"
    of: str | TermRef | None = None
    alias: str | None = Field(default=None, alias="as")

    @model_validator(mode="after")
    def _needs_an_argument(self) -> Metric:
        if self.fn == "count":
            if self.of is not None:
                raise ValueError("count takes no `of`; use count_distinct to count values")
        elif self.of is None:
            raise ValueError(f"{self.fn} needs an `of`")
        if isinstance(self.of, TermRef) and self.fn not in ("avg", "count_distinct"):
            raise ValueError(
                f"{self.fn} of a term makes no sense; avg of a term is the share of rows "
                "where it holds"
            )
        return self

    @property
    def name(self) -> str:
        if self.alias:
            return self.alias
        if self.of is None:
            return self.fn
        of = self.of.term if isinstance(self.of, TermRef) else self.of
        return f"{self.fn}_{of.replace('.', '__')}"


class OrderKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    dir: Literal["asc", "desc"] = "asc"


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str | None = None  # echoed into the envelope and the audit log only
    entity: str
    where: list[Clause] = Field(default_factory=list)
    seasons: SeasonSpan | None = None
    game_types: list[str] = Field(default_factory=lambda: ["REG"])
    group_by: list[GroupKey] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=lambda: [Metric(fn="count")])
    order_by: list[OrderKey] = Field(default_factory=list)
    limit: int | None = None
    allow_partial_coverage: bool = False
    sample_rows: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _parse_clauses(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if isinstance(data.get("where"), list):
            data["where"] = [parse_clause(c) for c in data["where"]]
        if isinstance(data.get("group_by"), list):
            data["group_by"] = [
                TermKey.model_validate(g) if isinstance(g, dict) else g for g in data["group_by"]
            ]
        return data

    @model_validator(mode="after")
    def _known_entity(self) -> QueryPlan:
        from chalktalk.entities import ENTITIES

        if self.entity not in ENTITIES:
            raise ValueError(
                f"unknown entity {self.entity!r}; expected one of {', '.join(ENTITIES)}"
            )
        if not self.metrics:
            raise ValueError("a plan needs at least one metric")
        return self

    def terms(self) -> list[TermRef]:
        """Every term the plan mentions, wherever it appears."""
        found: list[TermRef] = []

        def walk(clause: Clause) -> None:
            if isinstance(clause, TermRef):
                found.append(clause)
            elif isinstance(clause, Not):
                walk(clause.not_)
            elif isinstance(clause, AnyOf):
                for inner in clause.any_of:
                    walk(inner)
            elif isinstance(clause, AllOf):
                for inner in clause.all_of:
                    walk(inner)

        for clause in self.where:
            walk(clause)
        for key in self.group_by:
            if isinstance(key, TermKey):
                found.append(TermRef(term=key.term, basis=key.basis))
        for metric in self.metrics:
            if isinstance(metric.of, TermRef):
                found.append(metric.of)
        return found

    def attributes(self) -> list[str]:
        """Every attribute reference, including the ones outside `where`.

        Coverage has to cover metrics, grouping and ordering too, not just
        filters — a pitfall the plan calls out by name.
        """
        found: list[str] = []

        def walk(clause: Clause) -> None:
            if isinstance(clause, AttrRule):
                found.append(clause.attr)
            elif isinstance(clause, Not):
                walk(clause.not_)
            elif isinstance(clause, AnyOf):
                for inner in clause.any_of:
                    walk(inner)
            elif isinstance(clause, AllOf):
                for inner in clause.all_of:
                    walk(inner)

        for clause in self.where:
            walk(clause)
        found.extend(k for k in self.group_by if isinstance(k, str))
        found.extend(m.of for m in self.metrics if isinstance(m.of, str))
        return found


Not.model_rebuild()
AnyOf.model_rebuild()
AllOf.model_rebuild()
QueryPlan.model_rebuild()
