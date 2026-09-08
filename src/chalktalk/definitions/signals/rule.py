"""`rule` — an attribute compared to a value, and/or-ed together.

The simplest signal and the one most definitions bottom out in. It knows about
comparison operators and nothing else: `snaps_unit >= 1` is a rule, and so is
`game_type = 'REG'`, and neither mentions football.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chalktalk.definitions.signals.base import (
    AttrRef,
    CompileCtx,
    Compiled,
    Signal,
    ValidationCtx,
)
from chalktalk.definitions.spec import Definition, InvalidDefinition
from chalktalk.features.catalog import UnknownAttribute
from chalktalk.spec_types import SELF_NS

Op = Literal[
    "=", "!=", "<", "<=", ">", ">=", "in", "not_in", "between", "is_null", "is_not_null", "like"
]

#: Operators that take no value at all.
NULLARY = {"is_null", "is_not_null"}
#: Operators whose value is a list.
LIST_OPS = {"in", "not_in"}

_SQL_OP = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">=", "like": "LIKE"}

_ENGLISH_OP = {
    "=": "is",
    "!=": "is not",
    "<": "<",
    "<=": "≤",
    ">": ">",
    ">=": "≥",
    "like": "matches",
}

#: Which attribute types a value of each Python type may be compared with.
_COMPATIBLE: dict[str, tuple[type, ...]] = {
    "int": (int,),
    "float": (int, float),
    "bool": (bool,),
    "str": (str,),
    "date": (str,),
}


class RuleClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attr: str
    op: Op
    value: Any = None

    @field_validator("attr")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("attr is empty")
        return value

    @property
    def ref(self) -> AttrRef:
        namespace, _, name = self.attr.rpartition(".")
        return AttrRef(namespace or SELF_NS, name)


class RuleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match: Literal["all", "any"] = "all"
    rules: Annotated[list[RuleClause], Field(min_length=1)]


def _check_value(clause: RuleClause, attr_type: str, name: str | None) -> None:
    """A value has to be comparable with the attribute, or the answer is quietly wrong."""
    allowed = _COMPATIBLE[attr_type]
    field = f"rules.{clause.attr}"

    if clause.op in NULLARY:
        if clause.value is not None:
            raise InvalidDefinition(f"{clause.op} takes no value", name=name, field=field)
        return

    if clause.value is None:
        raise InvalidDefinition(f"{clause.op} needs a value", name=name, field=field)

    if clause.op == "between":
        if not isinstance(clause.value, list) or len(clause.value) != 2:
            raise InvalidDefinition(
                "between takes a list of exactly two values", name=name, field=field
            )
        values = clause.value
    elif clause.op in LIST_OPS:
        if not isinstance(clause.value, list) or not clause.value:
            raise InvalidDefinition(f"{clause.op} takes a non-empty list", name=name, field=field)
        values = clause.value
    else:
        values = [clause.value]

    for value in values:
        # bool is a subclass of int in Python, so check it first.
        if attr_type != "bool" and isinstance(value, bool):
            raise InvalidDefinition(
                f"{clause.attr} is {attr_type}, but the value is a boolean",
                name=name,
                field=field,
            )
        if not isinstance(value, allowed):
            raise InvalidDefinition(
                f"{clause.attr} is {attr_type}, but {value!r} is {type(value).__name__}",
                name=name,
                field=field,
            )
    if clause.op == "like" and attr_type != "str":
        raise InvalidDefinition(
            f"like needs a text attribute, not {attr_type}", name=name, field=field
        )


def _clause_sql(clause: RuleClause) -> tuple[str, list[Any]]:
    column = f'{{{clause.ref.namespace}}}."{clause.ref.name}"'
    if clause.op == "is_null":
        return f"{column} IS NULL", []
    if clause.op == "is_not_null":
        return f"{column} IS NOT NULL", []
    if clause.op == "between":
        return f"{column} BETWEEN ? AND ?", list(clause.value)
    if clause.op in LIST_OPS:
        holes = ", ".join("?" for _ in clause.value)
        negate = "NOT " if clause.op == "not_in" else ""
        return f"{column} {negate}IN ({holes})", list(clause.value)
    return f"{column} {_SQL_OP[clause.op]} ?", [clause.value]


def _clause_english(clause: RuleClause, nullable: bool) -> str:
    if clause.op == "is_null":
        return f"{clause.attr} is missing"
    if clause.op == "is_not_null":
        return f"{clause.attr} is present"
    if clause.op == "between":
        return f"{clause.attr} is between {clause.value[0]} and {clause.value[1]}"
    if clause.op in LIST_OPS:
        joined = ", ".join(repr(v) for v in clause.value)
        return f"{clause.attr} is {'not ' if clause.op == 'not_in' else ''}one of {joined}"
    text = f"{clause.attr} {_ENGLISH_OP[clause.op]} {clause.value!r}"
    if clause.op == "!=" and nullable:
        # SQL's three-valued logic surprises people, so say it rather than hide it.
        text += " (rows where it is missing do not match)"
    return text


class RuleSignal(Signal):
    id = "rule"
    Params = RuleParams

    def validate(self, d: Definition, ctx: ValidationCtx) -> None:
        params = self.parse(d)
        for clause in params.rules:
            try:
                attribute = ctx.attribute(clause.ref)
            except UnknownAttribute as exc:
                raise InvalidDefinition(
                    str(exc),
                    name=d.name,
                    field=f"rules.{clause.attr}",
                    did_you_mean=exc.did_you_mean,
                ) from exc
            _check_value(clause, attribute.type, d.name)

    def compile(self, d: Definition, ctx: CompileCtx) -> Compiled:
        params = self.parse(d)
        fragments, values, namespaces = [], [], set()
        for clause in params.rules:
            sql, clause_values = _clause_sql(clause)
            fragments.append(sql)
            values.extend(clause_values)
            namespaces.add(clause.ref.namespace)
        joiner = " AND " if params.match == "all" else " OR "
        predicate = joiner.join(fragments)
        if len(fragments) > 1:
            predicate = f"({predicate})"
        return Compiled(predicate, values, namespaces_used=namespaces)

    def explain(self, d: Definition, ctx: ValidationCtx) -> str:
        params = self.parse(d)
        parts = []
        for clause in params.rules:
            nullable = True
            try:
                ctx.attribute(clause.ref)
            except UnknownAttribute:
                pass
            parts.append(_clause_english(clause, nullable))
        return f" {'and' if params.match == 'all' else 'or'} ".join(parts)

    def requires(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]:
        return [clause.ref for clause in self.parse(d).rules]
