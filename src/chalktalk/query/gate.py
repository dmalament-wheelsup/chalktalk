"""The definition gate.

This is the mechanism the whole project is built around, and it is structural
rather than a prompt instruction: models are eager, and asked to answer a
question containing "star player" they will otherwise pick a reasonable-looking
default and move on. `check` refuses first and explains what to do next.

Two properties matter as much as the refusal itself. It reports **every**
problem of the first failing kind at once, so a caller with three unresolved
terms learns about all three rather than discovering them one round trip at a
time. And it checks coverage over attributes used *anywhere* in the plan —
metrics and grouping included — because a definition that cannot be computed for
the seasons asked about must not quietly return zero.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

import duckdb

from chalktalk.config import Settings
from chalktalk.coverage import Coverage, SeasonRange
from chalktalk.definitions.propose import propose
from chalktalk.definitions.signals import SIGNALS, AttrRef, ValidationCtx
from chalktalk.definitions.spec import Definition
from chalktalk.entities import ENTITIES, EntityMismatch, can_lift, lift_namespace, namespace_lag
from chalktalk.features.catalog import UnknownAttribute, attributes_for, resolve
from chalktalk.query.envelope import ErrorEnvelope
from chalktalk.query.plan import ANY_GAME_TYPE, GAME_TYPES, QueryPlan, TermRef
from chalktalk.spec_types import SELF_NS


@dataclass(frozen=True, order=True)
class CoverageRef:
    """A column the plan needs, and the season lag it is read at.

    ``lag`` is what makes a ``prior_season`` term honest: the column's coverage
    describes the seasons its *data* exists for, and reading it a season back
    shifts the seasons it can *answer* for by the same amount.
    """

    table: str
    column: str
    lag: int = 0

    @property
    def ref(self) -> str:
        return f"{self.table}.{self.column}"


#: Entities whose own table carries game_type.
GAME_TYPED = {"game", "team_game", "player_game"}

#: `play` is a game-typed entity too, but `pbp` spells it `season_type` with only
#: REG and POST, so the filter goes through the `game` join instead.
GAME_TYPE_VIA_GAME = {"play"}


@dataclass
class GateResult:
    ok: bool
    error: ErrorEnvelope | None = None
    seasons: SeasonRange | None = None
    excluded: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    definitions_used: list[Definition] = field(default_factory=list)
    #: term name (as written in the plan) -> the definition it resolved to
    resolved: dict[str, Definition] = field(default_factory=dict)


def _fail(code: str, message: str, **extra: Any) -> GateResult:
    return GateResult(ok=False, error=ErrorEnvelope(error=code, message=message, **extra))


def check(
    plan: QueryPlan,
    *,
    store,
    coverage: Coverage,
    settings: Settings,
    conn: duckdb.DuckDBPyConnection,
) -> GateResult:
    """Everything that must hold before a plan may run. Order matters."""
    # 1. invalid_plan
    problem = _plan_problems(plan, settings)
    if problem:
        return _fail("invalid_plan", problem)

    # 2. unknown_attribute — every reference, not only the ones in `where`
    unknown = _unknown_attributes(plan, conn)
    if unknown:
        return _fail(
            "unknown_attribute",
            f"{len(unknown)} attribute(s) do not exist on {plan.entity}",
            attributes=unknown,
        )

    # 3. unresolved_term — all of them at once
    wanted = plan.terms()
    missing = [t for t in wanted if store.get(t.term) is None and t.term not in store.broken]
    if missing:
        return _fail(
            "unresolved_term",
            f"{len(missing)} term(s) have no definition. Call propose_definition for each, "
            "then save the one you mean.",
            terms=[_suggestions_for(t.term, store) for t in missing],
            next_step="propose_definition",
        )

    # 4. definition_broken — transitively
    broken = _broken_terms(wanted, store)
    if broken:
        return _fail(
            "definition_broken",
            f"{len(broken)} definition(s) no longer compile against this database",
            definitions=broken,
        )

    used = _collect_definitions(wanted, store)

    # 5. entity_mismatch
    for term in wanted:
        definition = store.get(term.term)
        if not can_lift(definition.entity, plan.entity):
            allowed = [e for e in ENTITIES if can_lift(definition.entity, e)]
            return _fail(
                "entity_mismatch",
                f"{term.term!r} is a {definition.entity} definition and cannot be used in a "
                f"{plan.entity} query",
                definition=term.term,
                definition_entity=definition.entity,
                plan_entity=plan.entity,
                allowed_entities=allowed,
            )
        if term.basis is not None and definition.entity != "player_season":
            return _fail(
                "invalid_plan",
                f"basis applies only to player_season definitions; {term.term!r} is "
                f"{definition.entity}",
            )

    # 6. coverage_gap
    refs = _coverage_refs(plan, wanted, store, conn)
    covered = _intersect(coverage, refs)
    queryable = coverage.queryable_seasons()
    if not queryable:
        return _fail("coverage_gap", "no season in this database is queryable")

    requested = (
        SeasonRange(plan.seasons.first, plan.seasons.last)
        if plan.seasons
        else SeasonRange(queryable[0], queryable[-1])
    )
    if covered is None:
        limiting = _limiting(refs, coverage)
        return _fail(
            "coverage_gap",
            "nothing in this database can answer that: "
            + ", ".join(f"{r['ref']} has no data" for r in limiting[:3]),
            requested=[requested.first, requested.last],
            covered=None,
            limiting=limiting,
        )

    first, last = max(requested.first, covered.first), min(requested.last, covered.last)
    warnings: list[str] = []
    excluded: list[int] = []

    if first > last:
        return _fail(
            "coverage_gap",
            f"seasons {requested.first}-{requested.last} were asked for, but the attributes and "
            f"definitions used only overlap in {covered.first}-{covered.last}",
            requested=[requested.first, requested.last],
            covered=[covered.first, covered.last],
            limiting=_limiting(refs, coverage),
        )

    if (first, last) != (requested.first, requested.last):
        excluded = [s for s in range(requested.first, requested.last + 1) if s < first or s > last]
        if not plan.allow_partial_coverage:
            return _fail(
                "coverage_gap",
                f"seasons {requested.first}-{requested.last} were asked for, but this query can "
                f"only be answered for {first}-{last}. Pass allow_partial_coverage to run on "
                "the covered seasons.",
                requested=[requested.first, requested.last],
                covered=[first, last],
                excluded=excluded,
                limiting=_limiting(refs, coverage),
            )
        span = str(excluded[0]) if len(excluded) == 1 else f"{excluded[0]}-{excluded[-1]}"
        warnings.append(f"seasons {span} excluded: {_why_excluded(refs, coverage, first, last)}")

    # 7. warnings, never errors
    warnings.extend(_soft_warnings(plan, wanted, store, coverage, conn, first, last))

    return GateResult(
        ok=True,
        seasons=SeasonRange(first, last),
        excluded=excluded,
        warnings=warnings,
        definitions_used=used,
        resolved={t.term: store.get(t.term) for t in wanted},
    )


# ── the individual checks ─────────────────────────────────────────────────────


def _plan_problems(plan: QueryPlan, settings: Settings) -> str | None:
    if plan.limit is not None and plan.limit > settings.query_row_cap:
        return f"limit {plan.limit} exceeds the cap of {settings.query_row_cap}"
    if plan.limit is not None and plan.limit < 1:
        return "limit must be at least 1"
    if plan.sample_rows is not None and not 0 <= plan.sample_rows <= 50:
        return "sample_rows must be between 0 and 50"
    unknown = [g for g in plan.game_types if g not in GAME_TYPES and g != ANY_GAME_TYPE]
    if unknown:
        return (
            f"unknown game_type(s) {', '.join(unknown)}; expected "
            f"{', '.join(GAME_TYPES)} or {ANY_GAME_TYPE}"
        )
    if not plan.game_types:
        return "game_types is empty; use ['*'] for all game types"
    return None


def _unknown_attributes(plan: QueryPlan, conn) -> list[dict[str, Any]]:
    misses = []
    for ref in plan.attributes():
        try:
            resolve(plan.entity, ref, conn)
        except UnknownAttribute as exc:
            misses.append({"attr": ref, "did_you_mean": exc.did_you_mean})
    for key in plan.order_by:
        # An order key may name a metric alias or a group key rather than a column.
        known = (
            {m.name for m in plan.metrics}
            | {k for k in plan.group_by if isinstance(k, str)}
            | {k.term for k in plan.group_by if not isinstance(k, str)}
        )
        if key.key in known:
            continue
        try:
            resolve(plan.entity, key.key, conn)
        except UnknownAttribute:
            misses.append(
                {
                    "attr": key.key,
                    "did_you_mean": difflib.get_close_matches(key.key, sorted(known), n=3),
                }
            )
    return misses


def _suggestions_for(term: str, store) -> dict[str, Any]:
    proposal = propose(term, store=store)
    return {
        "term": term,
        "suggestions": [
            {"name": s.definition.name, "explanation": s.explanation} for s in proposal.suggestions
        ],
        "families": proposal.families,
        "not_computable": proposal.not_computable,
    }


def _broken_terms(wanted: list[TermRef], store) -> dict[str, str]:
    broken: dict[str, str] = {}
    for term in wanted:
        if term.term in store.broken:
            broken[term.term] = store.broken[term.term]
    return broken


def _collect_definitions(wanted: list[TermRef], store) -> list[Definition]:
    """Every definition the plan uses, transitively, in dependency order."""
    seen: dict[str, Definition] = {}

    def walk(name: str) -> None:
        if name in seen:
            return
        definition = store.get(name)
        if definition is None:
            return
        if definition.signal == "composite":
            for inner in definition.params.get("terms", []):
                walk(inner)
        seen[definition.name] = definition

    for term in wanted:
        walk(term.term)
    return list(seen.values())


def _ctx(entity: str, store, coverage, settings, conn) -> ValidationCtx:
    return ValidationCtx(
        entity=entity, conn=conn, coverage=coverage, store=store, settings=settings
    )


def _coverage_refs(plan, wanted, store, conn) -> list[CoverageRef]:
    """Every column the plan touches, definitions included, with its season lag.

    ``wanted`` must be the plan's *own* term references, not every definition
    reached transitively: `requires()` already recurses, and it deliberately
    stops at an `any_of`, where one branch suffices. Passing the flattened list
    back in would reinstate every branch as a hard requirement and refuse
    seasons the definition can in fact answer.

    Term references rather than definitions because the lag depends on the
    *basis the plan asked for*, which overrides the definition's own default.
    """
    refs: set[CoverageRef] = set()

    for ref in plan.attributes():
        namespace, _, name = ref.rpartition(".")
        namespace = namespace or SELF_NS
        table = _table_for(plan.entity, namespace)
        if table:
            refs.add(CoverageRef(table, name, namespace_lag(plan.entity, namespace)))

    for term in wanted:
        definition = store.get(term.term)
        basis = term.basis or definition.effective_basis
        signal = SIGNALS[definition.signal]
        ctx = _ctx(definition.entity, store, None, None, conn)
        try:
            required = signal.requires(definition, ctx)
        except Exception:  # noqa: BLE001 — a broken definition was already reported
            continue
        for attr_ref in required:
            table = _table_for(definition.entity, attr_ref.namespace)
            if not table:
                continue
            try:
                target = lift_namespace(
                    definition.entity, plan.entity, attr_ref.namespace, basis=basis
                )
            except EntityMismatch:  # already reported as entity_mismatch
                continue
            refs.add(CoverageRef(table, attr_ref.name, namespace_lag(plan.entity, target)))
    return sorted(refs)


def _intersect(coverage: Coverage, refs: list[CoverageRef]) -> SeasonRange | None:
    """The seasons every ref can answer for, each shifted by its own lag.

    Refs are grouped by lag and intersected within the group before shifting,
    so a `prior_season` term is measured against the seasons its data exists
    for and then moved forward to the seasons it can speak about.
    """
    queryable = coverage.queryable_seasons()
    if not queryable:
        return None
    first, last = queryable[0], queryable[-1]

    by_lag: dict[int, list[tuple[str, str]]] = {}
    for ref in refs:
        by_lag.setdefault(ref.lag, []).append((ref.table, ref.column))

    for lag, group in by_lag.items():
        got = coverage.intersect(group)
        if got is None:
            return None
        first = max(first, got.first + lag)
        last = min(last, got.last + lag)
    return SeasonRange(first, last) if first <= last else None


def _table_for(entity: str, namespace: str) -> str | None:
    spec = ENTITIES[entity]
    if namespace == SELF_NS:
        return spec.table
    join = spec.namespaces.get(namespace)
    return join.table if join else None


def _limiting(refs: list[CoverageRef], coverage: Coverage) -> list[dict[str, Any]]:
    """Which references actually constrain the answer, worst first.

    ``first``/``last`` are the seasons the ref can *answer* for, so a lagged ref
    reports its data window shifted. Without that the caller is told a column
    covers 2013 while the query it limits cannot run for 2013.
    """
    rows = []
    for ref in refs:
        found = coverage.column(ref.table, ref.column)
        if found is None or not found.seasonal:
            continue
        row = {
            "ref": ref.ref,
            "first": None if found.first_season is None else found.first_season + ref.lag,
            "last": None if found.last_season is None else found.last_season + ref.lag,
        }
        if ref.lag:
            row["lag"] = ref.lag
            row["data_first"], row["data_last"] = found.first_season, found.last_season
            row["why"] = (
                f"read {abs(ref.lag)} season{'s' if abs(ref.lag) > 1 else ''} "
                f"{'back' if ref.lag > 0 else 'forward'}"
            )
        rows.append(row)
    rows.sort(key=lambda r: (r["first"] is not None, -(r["first"] or 0)))
    return rows


def _why_excluded(refs: list[CoverageRef], coverage: Coverage, first: int, last: int) -> str:
    """Name what actually pushed the range in, preferring a lagged ref.

    A season dropped because a term reads a season back is not the same as one
    dropped for missing data, and saying "no data" for it sends the reader
    looking for a hole in the database that is not there.
    """
    for ref in _limiting(refs, coverage):
        if ref.get("lag") and (ref["first"] == first or ref["last"] == last):
            direction = "before" if ref["lag"] > 0 else "after"
            return (
                f"{ref['ref']} is {ref['why']}, and there is no season "
                f"{direction} {ref['data_first'] if ref['lag'] > 0 else ref['data_last']} "
                "in this database"
            )
    return "no data for the attributes used"


def _soft_warnings(plan, wanted, store, coverage, conn, first: int, last: int) -> list[str]:
    warnings: list[str] = []

    if plan.game_types != ["REG"] and plan.entity not in (GAME_TYPED | GAME_TYPE_VIA_GAME):
        warnings.append(
            f"{plan.entity} has no game_type, so game_types was ignored "
            f"({', '.join(plan.game_types)})"
        )

    status = coverage.status(last)
    if status is not None and status.in_progress:
        warnings.append(f"{last} is still in progress, so its numbers will change")

    for term in wanted:
        definition = store.get(term.term)
        signal = SIGNALS[definition.signal]
        ctx = _ctx(definition.entity, store, coverage, None, conn)
        try:
            preferred = signal.prefers(definition, ctx)
        except Exception:  # noqa: BLE001
            continue
        for attr_ref in preferred:
            table = _table_for(definition.entity, attr_ref.namespace)
            found = coverage.column(table, attr_ref.name) if table else None
            if found and found.seasonal and found.first_season and found.first_season > first:
                warnings.append(
                    f"{definition.name} works better from {found.first_season}: "
                    f"{table}.{attr_ref.name} has no data before then"
                )

    for ref in _coverage_refs(plan, wanted, store, conn):
        found = coverage.column(ref.table, ref.column)
        if found and found.has_gaps:
            warnings.append(f"{ref.ref} has seasons with no data inside its range")

    return warnings


__all__ = ["AttrRef", "GateResult", "attributes_for", "check"]
