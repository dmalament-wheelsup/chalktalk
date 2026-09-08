"""`rank` — the top or bottom N of a cohort.

"The team's leading rusher" is a rank, not a percentile: the question is about
position in an ordering, not a share of the distribution. Ties break on the
entity key so the answer is the same every run.
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

#: DESC for `top` so row 1 is the largest. The order is the opposite of
#: percentile's, where the largest value needs the highest cume_dist.
_ORDERED = "row_number() OVER (PARTITION BY {cohort} ORDER BY {attr} {order}, {tiebreak})"


class RankParams(CohortParams):
    model_config = ConfigDict(extra="forbid")

    top_n: int = Field(ge=1)


class RankSignal(Signal):
    id = "rank"
    Params = RankParams

    def validate(self, d: Definition, ctx: ValidationCtx) -> None:
        params = self.parse(d)
        check_cohort(d, ctx, params)
        validate_eligibility(d, ctx, params)

    def compile(self, d: Definition, ctx: CompileCtx) -> Compiled:
        from chalktalk.entities import ENTITIES

        params = self.parse(d)
        tiebreak = ", ".join(f'"{k}"' for k in ENTITIES[d.entity].key)
        ordered = _ORDERED.replace("{tiebreak}", tiebreak)
        # row_number wants DESC for "top", the reverse of cume_dist.
        flipped = params.model_copy(
            update={"direction": "bottom" if params.direction == "top" else "top"}
        )
        cte, keys = cohort_cte(d, ctx, flipped, ordered_value=ordered, prefix="rank")
        predicate = membership_predicate(cte, keys, "pos <= ?")
        return Compiled(predicate, params=[params.top_n], ctes=[cte], namespaces_used={SELF_NS})

    def explain(self, d: Definition, ctx: ValidationCtx) -> str:
        params = self.parse(d)
        where = " and ".join(params.cohort)
        side = "highest" if params.direction == "top" else "lowest"
        head = (
            f"#1 by {params.attr}"
            if params.top_n == 1
            else f"{side} {params.top_n} by {params.attr}"
        )
        text = f"{head} within {where}"
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
