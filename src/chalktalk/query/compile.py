"""Turning a gated plan into parameterized SQL.

Only two kinds of thing reach the SQL text: identifiers that came from the
catalog, and `?`. Every literal a caller supplied is a bound parameter, so a
plan cannot smuggle SQL through a value.

One `base` CTE does all the joining and filtering and projects everything the
outer query needs — group keys and metric inputs included, under flat names —
so the aggregate never has to reach back through a join. The sample query reads
the same `base`, which is what makes the returned rows provably the rows that
were counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from chalktalk.config import Settings
from chalktalk.coverage import SeasonRange
from chalktalk.definitions.signals import SIGNALS, CompileCtx, ValidationCtx
from chalktalk.definitions.signals.base import lift_compiled
from chalktalk.definitions.signals.rule import RuleClause, _clause_sql
from chalktalk.definitions.spec import Definition
from chalktalk.entities import ENTITIES, lift_namespace
from chalktalk.query.gate import GAME_TYPE_VIA_GAME, GAME_TYPED, GateResult
from chalktalk.query.plan import (
    AllOf,
    AnyOf,
    AttrRule,
    Metric,
    Not,
    QueryPlan,
    TermKey,
    TermRef,
)
from chalktalk.spec_types import SELF_NS

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@dataclass
class CompiledQuery:
    sql: str
    params: list[Any]
    sample_sql: str
    sample_params: list[Any]
    count_sql: str
    count_params: list[Any]
    definitions_used: list[Definition] = field(default_factory=list)
    seasons: SeasonRange | None = None
    warnings: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


@dataclass
class _Projection:
    """A column `base` computes so the outer query can just name it."""

    name: str
    sql: str
    params: list[Any]


class _Builder:
    def __init__(self, plan: QueryPlan, gate: GateResult, store, settings: Settings, conn) -> None:
        self.plan = plan
        self.gate = gate
        self.store = store
        self.settings = settings
        self.conn = conn
        self.spec = ENTITIES[plan.entity]
        self.used_namespaces: set[str] = set()
        self.ctes: dict[str, tuple[str, list[Any]]] = {}

    # -- names --------------------------------------------------------------

    def alias_of(self, namespace: str) -> str:
        if namespace in (SELF_NS, self.plan.entity):
            return self.spec.alias
        join = self.spec.namespaces.get(namespace)
        if join is None:
            raise KeyError(f"{self.plan.entity} has no namespace {namespace!r}")
        self.used_namespaces.add(namespace)
        return join.alias

    def column(self, ref: str) -> str:
        namespace, _, name = ref.rpartition(".")
        return f'{self.alias_of(namespace or SELF_NS)}."{name}"'

    @staticmethod
    def flat(ref: str) -> str:
        """`prior.snap_share_mean` becomes `prior__snap_share_mean`."""
        return ref.replace(".", "__")

    def resolve(self, sql: str) -> str:
        return _PLACEHOLDER.sub(lambda m: self.alias_of(m.group(1)), sql)

    # -- clauses ------------------------------------------------------------

    def clause(self, clause) -> tuple[str, list[Any]]:
        if isinstance(clause, AttrRule):
            sql, params = _clause_sql(
                RuleClause(attr=clause.attr, op=clause.op, value=clause.value)
            )
            return self.resolve(sql), params
        if isinstance(clause, TermRef):
            return self.term(clause)
        if isinstance(clause, Not):
            inner, params = self.clause(clause.not_)
            return f"NOT ({inner})", params
        if isinstance(clause, AnyOf | AllOf):
            inner = clause.any_of if isinstance(clause, AnyOf) else clause.all_of
            joiner = " OR " if isinstance(clause, AnyOf) else " AND "
            parts = [self.clause(c) for c in inner]
            params: list[Any] = []
            for _, values in parts:
                params.extend(values)
            return "(" + joiner.join(p[0] for p in parts) + ")", params
        raise TypeError(f"unhandled clause {type(clause).__name__}")

    def term(self, ref: TermRef) -> tuple[str, list[Any]]:
        definition = self.gate.resolved[ref.term]
        basis = ref.basis or definition.effective_basis
        compiled = SIGNALS[definition.signal].compile(definition, self._term_ctx(definition, basis))
        # The term compiled against its own entity; lift it into the plan's, so a
        # prior_season player_season term reads from the `prior` join and not
        # from the player_game row, where its column does not exist.
        compiled = lift_compiled(compiled, definition, self.plan.entity, basis)
        for cte in compiled.ctes:
            self.ctes.setdefault(cte.name, (cte.sql, cte.params))
        return self.resolve(compiled.predicate_sql), list(compiled.params)

    def _term_ctx(self, definition: Definition, basis: str) -> CompileCtx:
        def alias_of(namespace: str) -> str:
            target = lift_namespace(definition.entity, self.plan.entity, namespace, basis=basis)
            return self.alias_of(target if target is not None else SELF_NS)

        return CompileCtx(
            entity=definition.entity,
            conn=self.conn,
            coverage=None,
            store=self.store,
            settings=self.settings,
            alias_of=alias_of,
        )

    def joins(self) -> list[str]:
        return [
            f'LEFT JOIN "{self.spec.namespaces[ns].table}" {self.spec.namespaces[ns].alias} '
            f"ON {self.spec.namespaces[ns].on}"
            for ns in sorted(self.used_namespaces)
        ]


def compile_plan(
    plan: QueryPlan, gate: GateResult, *, store, settings: Settings, conn
) -> CompiledQuery:
    """A gated plan becomes SQL. Never call this on a plan the gate refused."""
    if not gate.ok:
        raise ValueError("cannot compile a plan the gate refused")

    b = _Builder(plan, gate, store, settings, conn)
    alias = b.spec.alias

    # Everything `base` projects beyond the entity's own columns, in the order
    # its parameters must bind.
    projections: list[_Projection] = []
    group_names: list[str] = []
    for key in plan.group_by:
        if isinstance(key, TermKey):
            sql, params = b.term(TermRef(term=key.term, basis=key.basis))
            projections.append(_Projection(key.term, sql, params))
            group_names.append(key.term)
        else:
            name = b.flat(key)
            if "." in key:
                projections.append(_Projection(name, b.column(key), []))
            group_names.append(name)

    metric_sql: list[str] = []
    for metric in plan.metrics:
        sql, extra = _metric(b, metric, projections)
        metric_sql.append(f'{sql} AS "{metric.name}"')
        projections.extend(extra)

    where: list[str] = []
    where_params: list[Any] = []
    for clause in plan.where:
        sql, params = b.clause(clause)
        where.append(sql)
        where_params.extend(params)

    conditions = [f'{alias}."season" BETWEEN ? AND ?']
    base_params: list[Any] = [gate.seasons.first, gate.seasons.last]
    if plan.game_types != ["*"]:
        # `play` has no game_type of its own — `pbp` spells it `season_type` and
        # only distinguishes REG from POST — so it reaches through the game join.
        game_type_alias = (
            b.alias_of("game")
            if plan.entity in GAME_TYPE_VIA_GAME
            else alias
            if plan.entity in GAME_TYPED
            else None
        )
        if game_type_alias is not None:
            holes = ", ".join("?" for _ in plan.game_types)
            conditions.append(f'{game_type_alias}."game_type" IN ({holes})')
            base_params.extend(plan.game_types)
    conditions.extend(where)

    # Joins are only known once every expression has been built.
    projected = "".join(f',\n         {p.sql} AS "{p.name}"' for p in projections)
    join_sql = "".join(f"\n  {j}" for j in b.joins())
    base_sql = (
        f"base AS (\n  SELECT {alias}.*{projected}\n"
        f'  FROM "{b.spec.table}" {alias}{join_sql}\n'
        f"  WHERE {' AND '.join(conditions)}\n)"
    )
    base_bindings = [p for proj in projections for p in proj.params] + base_params + where_params

    cte_sql = [f"{name} AS ({sql})" for name, (sql, _) in b.ctes.items()]
    cte_params = [p for _, (_, values) in b.ctes.items() for p in values]
    with_sql = "WITH " + ",\n".join([*cte_sql, base_sql])

    select = [f'"{name}"' for name in group_names] + metric_sql
    limit = plan.limit or settings.query_default_limit
    order = _order_by(plan, group_names)
    sql = (
        f"{with_sql}\nSELECT {', '.join(select)}\nFROM base"
        + (f"\nGROUP BY {', '.join(f'"{n}"' for n in group_names)}" if group_names else "")
        + (f"\nORDER BY {order}" if order else "")
        + "\nLIMIT ?"
    )

    sample_columns = _sample_columns(plan, gate, b, store, conn)
    sample_rows = plan.sample_rows if plan.sample_rows is not None else settings.sample_rows
    sample_sql = (
        f"{with_sql}\nSELECT {', '.join(f'"{c}"' for c in sample_columns)}\nFROM base"
        f"\nORDER BY {_sample_order(plan)}\nLIMIT ?"
    )

    shared = cte_params + base_bindings
    return CompiledQuery(
        sql=sql,
        params=shared + [limit],
        sample_sql=sample_sql,
        sample_params=shared + [sample_rows],
        count_sql=f"{with_sql}\nSELECT count(*) FROM base",
        count_params=list(shared),
        definitions_used=gate.definitions_used,
        seasons=gate.seasons,
        warnings=list(gate.warnings),
        columns=group_names + [m.name for m in plan.metrics],
    )


def _metric(
    b: _Builder, metric: Metric, existing: list[_Projection]
) -> tuple[str, list[_Projection]]:
    """The aggregate expression, plus anything `base` has to project for it."""
    if metric.fn == "count":
        return "count(*)", []

    if isinstance(metric.of, TermRef):
        name = f"term__{metric.of.term}"
        already = {p.name for p in existing}
        extra: list[_Projection] = []
        if name not in already:
            sql, params = b.term(metric.of)
            extra.append(_Projection(name, sql, params))
        if metric.fn == "avg":
            # A NULL predicate is unknown, not false, so it is left out of the
            # rate rather than counted as a miss.
            return (
                f'avg(CASE WHEN "{name}" THEN 1.0 WHEN NOT "{name}" THEN 0.0 END)',
                extra,
            )
        return f'count(DISTINCT CASE WHEN "{name}" THEN 1 END)', extra

    name = b.flat(metric.of)
    extra = []
    if "." in metric.of and name not in {p.name for p in existing}:
        extra.append(_Projection(name, b.column(metric.of), []))
    function = "count(DISTINCT " if metric.fn == "count_distinct" else f"{metric.fn}("
    return f'{function}"{name}")', extra


def _order_by(plan: QueryPlan, group_names: list[str]) -> str:
    if plan.order_by:
        return ", ".join(f'"{k.key}" {k.dir.upper()}' for k in plan.order_by)
    return ", ".join(f'"{n}"' for n in group_names)


def _sample_order(plan: QueryPlan) -> str:
    if plan.entity in ("team_season", "player_season"):
        return '"season"'
    return '"season", "week"'


def _sample_columns(plan, gate, b: _Builder, store, conn) -> list[str]:
    """Enough to recognise a row and see why it qualified."""
    columns = list(b.spec.identifying)
    for definition in gate.definitions_used:
        if definition.entity != plan.entity:
            continue
        ctx = ValidationCtx(
            entity=definition.entity, conn=conn, coverage=None, store=store, settings=b.settings
        )
        try:
            refs = SIGNALS[definition.signal].evidence(definition, ctx)
        except Exception:  # noqa: BLE001 — evidence is a nicety, not a requirement
            continue
        columns.extend(r.name for r in refs if r.namespace == SELF_NS)
    columns.extend(ref for ref in plan.attributes() if "." not in ref)
    return list(dict.fromkeys(columns))
