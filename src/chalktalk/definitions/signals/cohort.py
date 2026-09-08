"""Shared machinery for the two cohort signals, `percentile` and `rank`.

Both ask the same question — where does this row sit among its peers — and
differ only in how they cut. Both compute at query time over a CTE, so any
numeric attribute can be ranked within any cohort without a rebuild (D13).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from chalktalk.definitions.signals.base import CTE, AttrRef, CompileCtx, ValidationCtx
from chalktalk.definitions.signals.rule import RuleParams, RuleSignal
from chalktalk.definitions.spec import Definition, InvalidDefinition
from chalktalk.entities import ENTITIES
from chalktalk.features.catalog import UnknownAttribute
from chalktalk.spec_types import SELF_NS

Direction = Literal["top", "bottom"]

NUMERIC = {"int", "float"}


class CohortParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attr: str
    cohort: list[str] = Field(default_factory=list)
    direction: Direction = "top"
    eligible: dict[str, Any] | None = None


def check_cohort(d: Definition, ctx: ValidationCtx, params: CohortParams) -> None:
    """`attr` and every cohort column must be plain columns of the entity's own table."""
    for field, name in [("attr", params.attr), *(("cohort", c) for c in params.cohort)]:
        if "." in name:
            raise InvalidDefinition(
                f"{name!r} is namespaced; {field} must be an attribute of "
                f"{d.entity} itself, because the cohort is computed over that table",
                name=d.name,
                field=field,
            )
        try:
            attribute = ctx.attribute(AttrRef(SELF_NS, name))
        except UnknownAttribute as exc:
            raise InvalidDefinition(
                str(exc), name=d.name, field=field, did_you_mean=exc.did_you_mean
            ) from exc
        if field == "attr" and attribute.type not in NUMERIC:
            raise InvalidDefinition(
                f"{name} is {attribute.type}; a cohort can only be ordered by a number",
                name=d.name,
                field="attr",
            )
    if not params.cohort:
        raise InvalidDefinition(
            "cohort is empty: name the columns the comparison is made within, "
            "for example season and position_group",
            name=d.name,
            field="cohort",
        )


def eligibility(d: Definition, ctx: ValidationCtx, params: CohortParams) -> RuleParams | None:
    """The rows a cohort is computed over.

    The default for player_season is the era-neutral one from D13/D24: at least
    half the team's games, which is the same fraction whether the season is 16
    games or 17. Other entities have no default.
    """
    if params.eligible is not None:
        return RuleParams.model_validate(params.eligible)
    if d.entity == "player_season":
        return RuleParams.model_validate(
            {
                "match": "all",
                "rules": [
                    {
                        "attr": "games_played_share",
                        "op": ">=",
                        "value": ctx.settings.pctile_default_min_share,
                    }
                ],
            }
        )
    return None


def validate_eligibility(d: Definition, ctx: ValidationCtx, params: CohortParams) -> None:
    rules = eligibility(d, ctx, params)
    if rules is None:
        return
    probe = d.model_copy(update={"signal": "rule", "params": rules.model_dump()})
    RuleSignal().validate(probe, ctx)


def cohort_cte(
    d: Definition,
    ctx: CompileCtx,
    params: CohortParams,
    *,
    ordered_value: str,
    prefix: str,
) -> tuple[CTE, list[str]]:
    """A CTE over the definition's own entity table, keyed and ranked.

    ``ordered_value`` is the window expression that decides membership; the
    caller supplies it because that is the only thing percentile and rank
    disagree about.
    """
    spec = ENTITIES[d.entity]
    keys = list(spec.key)
    cohort_sql = ", ".join(f'"{c}"' for c in params.cohort)
    order = "ASC" if params.direction == "top" else "DESC"

    conditions = [f'"{params.attr}" IS NOT NULL']
    conditions += [f'"{c}" IS NOT NULL' for c in params.cohort]
    values: list[Any] = []

    rules = eligibility(d, ctx, params)
    if rules is not None:
        probe = d.model_copy(update={"signal": "rule", "params": rules.model_dump()})
        compiled = RuleSignal().compile(probe, ctx)
        # The CTE is a bare scan of one table, so `self` is that table.
        conditions.append(compiled.predicate_sql.replace("{" + SELF_NS + "}", "t"))
        values.extend(compiled.params)

    key_sql = ", ".join(f'"{k}"' for k in keys)
    sql = (
        f"SELECT {key_sql}, "
        f"{ordered_value.format(attr=f'"{params.attr}"', cohort=cohort_sql, order=order)} AS pos "
        f'FROM "{spec.table}" t WHERE {" AND ".join(conditions)}'
    )
    return CTE(f"{prefix}_{d.name}", sql, values), keys


def membership_predicate(cte: CTE, keys: list[str], comparison: str) -> str:
    """`(self.k1, self.k2) IN (SELECT k1, k2 FROM cte WHERE ...)`."""
    outer = ", ".join(f'{{{SELF_NS}}}."{k}"' for k in keys)
    inner = ", ".join(f'"{k}"' for k in keys)
    lhs = f"({outer})" if len(keys) > 1 else outer
    return f"{lhs} IN (SELECT {inner} FROM {cte.name} WHERE {comparison})"
