"""What a query returns, and what a refusal says.

The model narrates; the envelope makes the narration checkable. For this class
of question an unverifiable number is worse than no number, so every result
carries the definitions it used, the seasons it actually covered, a sample of
matched rows, and the SQL.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ErrorCode = Literal[
    "unresolved_term",
    "unknown_attribute",
    "definition_broken",
    "entity_mismatch",
    "coverage_gap",
    "invalid_plan",
    "invalid_definition",
    "sql_rejected",
    "sql_timeout",
    "no_database",
]


class ErrorEnvelope(BaseModel):
    """A refusal that says what to do next, never a silent default."""

    model_config = ConfigDict(extra="allow")

    ok: Literal[False] = False
    error: ErrorCode
    message: str

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class DefinitionUsed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: int
    entity: str
    basis: str | None = None
    signal: str
    explanation: str
    coverage: dict[str, int] | None = None


class SeasonReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: list[int] | None = None
    covered: list[int]
    excluded: list[int] = Field(default_factory=list)
    partial: bool = False
    includes_incomplete_season: bool = False


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_matched: int
    columns: list[str]
    rows: list[dict[str, Any]]


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    question: str | None = None
    entity: str
    english: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    definitions_used: list[DefinitionUsed] = Field(default_factory=list)
    seasons: SeasonReport
    game_types: list[str] = Field(default_factory=list)
    sample: Sample | None = None
    sql: str = ""
    sql_params: list[Any] = Field(default_factory=list)
    sample_sql: str | None = None
    timing_ms: dict[str, int] | None = None
    build: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


_ENTITY_NOUN = {
    "game": "games",
    "team_game": "team-games",
    "team_season": "team-seasons",
    "player_game": "player-games",
    "player_season": "player-seasons",
    "play": "plays",
}

_METRIC_NOUN = {
    "count": "Count",
    "count_distinct": "Distinct count",
    "sum": "Total",
    "avg": "Average",
    "min": "Minimum",
    "max": "Maximum",
}


def render_english(plan, seasons, game_types: list[str]) -> str:
    """One sentence describing what was actually asked and answered."""
    from chalktalk.query.plan import AllOf, AnyOf, AttrRule, Not, TermKey, TermRef

    def clause_text(clause) -> str:
        if isinstance(clause, TermRef):
            suffix = " [prior season]" if clause.basis == "prior_season" else ""
            return f"{clause.term}{suffix}"
        if isinstance(clause, AttrRule):
            if clause.op in ("is_null", "is_not_null"):
                return f"{clause.attr} {clause.op.replace('_', ' ')}"
            return f"{clause.attr} {clause.op} {clause.value!r}"
        if isinstance(clause, Not):
            return f"not ({clause_text(clause.not_)})"
        if isinstance(clause, AnyOf):
            return "(" + " or ".join(clause_text(c) for c in clause.any_of) + ")"
        if isinstance(clause, AllOf):
            return "(" + " and ".join(clause_text(c) for c in clause.all_of) + ")"
        return "?"

    metrics = []
    for metric in plan.metrics:
        head = _METRIC_NOUN[metric.fn]
        if metric.of is None:
            metrics.append(f"{head} of {_ENTITY_NOUN.get(plan.entity, plan.entity)}")
        elif isinstance(metric.of, TermRef):
            metrics.append(f"share of rows where {metric.of.term}")
        else:
            metrics.append(f"{head.lower()} {metric.of}")
    head = metrics[0][0].upper() + metrics[0][1:]
    if len(metrics) > 1:
        head += " and " + ", ".join(metrics[1:])

    scope = ", ".join(game_types) if game_types != ["*"] else "all game types"
    span = f"{seasons.first}–{seasons.last}" if seasons else "all seasons"
    text = f"{head} ({scope}, {span})"

    if plan.where:
        text += " where " + " and ".join(clause_text(c) for c in plan.where)

    if plan.group_by:
        keys = [k.term if isinstance(k, TermKey) else k for k in plan.group_by]
        text += ", by " + " and ".join(keys)

    return text + "."
