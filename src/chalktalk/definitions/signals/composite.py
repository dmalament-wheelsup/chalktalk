"""`composite` — other definitions, combined.

This is what keeps football out of the code. `early_exit` is not an algorithm;
it is `played AND regular AND (left_early OR snap_drop) AND corroborated`, and
every one of those parts is a definition the user can inspect, disagree with and
replace. If a shipped concept ever needs code of its own, the feature layer is
missing an attribute (D22).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chalktalk.definitions.signals.base import (
    AttrRef,
    CompileCtx,
    Compiled,
    Signal,
    ValidationCtx,
    lift_compiled,
)
from chalktalk.definitions.spec import Definition, InvalidDefinition
from chalktalk.entities import EntityMismatch, can_lift

Op = Literal["all_of", "any_of", "not"]

_JOINER = {"all_of": " AND ", "any_of": " OR "}
_ENGLISH = {"all_of": " and ", "any_of": " or "}


class CompositeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Op
    terms: Annotated[list[str], Field(min_length=1)]

    @model_validator(mode="after")
    def _not_takes_one(self) -> CompositeParams:
        if self.op == "not" and len(self.terms) != 1:
            raise ValueError("not takes exactly one term")
        if len(set(self.terms)) != len(self.terms):
            raise ValueError("a term is repeated")
        return self


def _resolve(d: Definition, ctx: ValidationCtx, name: str) -> Definition:
    term = ctx.store.get(name)
    if term is None:
        broken = ctx.store.broken.get(name)
        if broken is not None:
            raise InvalidDefinition(
                f"{name!r} is quarantined and cannot be used: {broken}",
                name=d.name,
                field="terms",
            )
        known = [s.name for s in ctx.store.list(include_broken=False)]
        import difflib

        raise InvalidDefinition(
            f"no definition named {name!r}",
            name=d.name,
            field="terms",
            did_you_mean=difflib.get_close_matches(name, known, n=3),
        )
    return term


def _check_no_cycle(d: Definition, ctx: ValidationCtx, seen: tuple[str, ...] = ()) -> None:
    if d.name in seen:
        loop = " -> ".join([*seen, d.name])
        raise InvalidDefinition(f"definitions refer to each other in a loop: {loop}", name=d.name)
    if d.signal != "composite":
        return
    params = CompositeParams.model_validate(d.params)
    for name in params.terms:
        term = ctx.store.get(name)
        if term is not None:
            _check_no_cycle(term, ctx, (*seen, d.name))


class CompositeSignal(Signal):
    id = "composite"
    Params = CompositeParams

    def validate(self, d: Definition, ctx: ValidationCtx) -> None:
        params = self.parse(d)
        _check_no_cycle(d, ctx)
        for name in params.terms:
            term = _resolve(d, ctx, name)
            if not can_lift(term.entity, d.entity):
                allowed = _liftable_into(term.entity)
                raise InvalidDefinition(
                    f"{name!r} is a {term.entity} definition and cannot be used in a "
                    f"{d.entity} one; it works in: {', '.join(allowed)}",
                    name=d.name,
                    field="terms",
                )

    def compile(self, d: Definition, ctx: CompileCtx) -> Compiled:
        from chalktalk.definitions.signals import SIGNALS

        params = self.parse(d)
        fragments: list[str] = []
        values: list[object] = []
        ctes = []
        namespaces: set[str] = set()
        terms_used: list[str] = []

        for name in params.terms:
            term = _resolve(d, ctx, name)
            compiled = SIGNALS[term.signal].compile(term, _term_ctx(term, ctx))
            compiled = lift_compiled(compiled, term, d.entity, term.effective_basis)
            fragments.append(compiled.predicate_sql)
            values.extend(compiled.params)
            ctes.extend(compiled.ctes)
            namespaces |= compiled.namespaces_used
            terms_used.extend([*compiled.terms_used, name])

        if params.op == "not":
            predicate = f"NOT ({fragments[0]})"
        else:
            predicate = "(" + _JOINER[params.op].join(fragments) + ")"

        # A term used twice through different paths only needs compiling once.
        seen: set[str] = set()
        ordered_terms = [t for t in terms_used if not (t in seen or seen.add(t))]
        return Compiled(predicate, values, ctes, namespaces, ordered_terms)

    def explain(self, d: Definition, ctx: ValidationCtx) -> str:
        params = self.parse(d)
        if params.op == "not":
            return f"not {params.terms[0]}"
        return _ENGLISH[params.op].join(params.terms)

    def requires(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]:
        from chalktalk.definitions.signals import SIGNALS

        refs: list[AttrRef] = []
        for name in self.parse(d).terms:
            term = ctx.store.get(name)
            if term is None:
                continue
            inner = ValidationCtx(
                entity=term.entity,
                conn=ctx.conn,
                coverage=ctx.coverage,
                store=ctx.store,
                settings=ctx.settings,
            )
            refs.extend(SIGNALS[term.signal].requires(term, inner))
        return refs


def _term_ctx(term: Definition, ctx: CompileCtx) -> CompileCtx:
    """Compile a term against its own entity, then lift the result."""
    return CompileCtx(
        entity=term.entity,
        conn=ctx.conn,
        coverage=ctx.coverage,
        store=ctx.store,
        settings=ctx.settings,
        alias_of=ctx.alias_of,
    )


def _liftable_into(entity: str) -> list[str]:
    from chalktalk.entities import ENTITIES

    return [e for e in ENTITIES if can_lift(entity, e)]


__all__ = ["CompositeParams", "CompositeSignal", "EntityMismatch"]
