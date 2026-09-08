"""`percentile` — an attribute in the top or bottom N% of its cohort.

The signal behind "star": whatever a star is, it is someone near the top of some
distribution. Which attribute and which cohort is the definition's business, not
this signal's.

**Ties use `cume_dist`, not `percent_rank`.** With 70 eligible quarterbacks and
15 tied at a snap share of 1.0, `percent_rank` puts that whole tied group at
about 0.80, so "top 10%" returns nobody at all. `cume_dist` counts the group as
reaching the top and returns all 15. A definition that silently matches nothing
is precisely the failure this project exists to prevent.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from chalktalk.definitions.signals.base import (
    AttrRef,
    CompileCtx,
    Compiled,
    Signal,
    ValidationCtx,
)
from chalktalk.definitions.signals.cohort import (
    CohortParams,
    check_cohort,
    cohort_cte,
    eligibility,
    membership_predicate,
    validate_eligibility,
)
from chalktalk.definitions.spec import Definition
from chalktalk.spec_types import SELF_NS

#: Ascending for `top` so that the largest values have the highest cume_dist.
_ORDERED = "cume_dist() OVER (PARTITION BY {cohort} ORDER BY {attr} {order})"


class PercentileParams(CohortParams):
    model_config = ConfigDict(extra="forbid")

    pctile: float = Field(ge=0, le=100)


class PercentileSignal(Signal):
    id = "percentile"
    Params = PercentileParams

    def validate(self, d: Definition, ctx: ValidationCtx) -> None:
        params = self.parse(d)
        check_cohort(d, ctx, params)
        validate_eligibility(d, ctx, params)

    def compile(self, d: Definition, ctx: CompileCtx) -> Compiled:
        params = self.parse(d)
        cte, keys = cohort_cte(d, ctx, params, ordered_value=_ORDERED, prefix="pct")
        # `top 90` means cume_dist >= 0.90; `bottom 90` orders the other way and
        # means at or below the 90th percentile from the bottom.
        threshold = (
            params.pctile / 100 if params.direction == "top" else (100 - params.pctile) / 100
        )
        predicate = membership_predicate(cte, keys, "pos >= ?")
        return Compiled(
            predicate,
            params=[threshold],
            ctes=[cte],
            namespaces_used={SELF_NS},
        )

    def explain(self, d: Definition, ctx: ValidationCtx) -> str:
        params = self.parse(d)
        where = " and ".join(params.cohort)
        side = "at or above" if params.direction == "top" else "at or below"
        text = f"{params.attr} {side} the {_ordinal(params.pctile)} percentile within {where}"
        rules = eligibility(d, ctx, params)
        if rules is not None:
            from chalktalk.definitions.signals.rule import RuleSignal

            probe = d.model_copy(update={"signal": "rule", "params": rules.model_dump()})
            text += f" (among rows where {RuleSignal().explain(probe, ctx)})"
        return text

    def requires(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]:
        params = self.parse(d)
        refs = [AttrRef(SELF_NS, params.attr)]
        refs += [AttrRef(SELF_NS, c) for c in params.cohort]
        rules = eligibility(d, ctx, params)
        if rules is not None:
            refs += [clause.ref for clause in rules.rules]
        return refs

    def evidence(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]:
        params = self.parse(d)
        return [AttrRef(SELF_NS, params.attr), *(AttrRef(SELF_NS, c) for c in params.cohort)]


def _ordinal(value: float) -> str:
    number = int(value) if float(value).is_integer() else value
    if isinstance(number, float):
        return f"{number}th"
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"
