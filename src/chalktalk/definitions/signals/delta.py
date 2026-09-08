"""`delta` — an attribute measured against a baseline.

The signal behind "played fewer snaps than usual". Which baseline is the
definition's business: `baseline_share` for a within-game comparison,
`prior.snap_share_mean` for a year-on-year one.

`min_baseline` exists because ratios are meaningless when the baseline is near
zero: a backup who normally plays 2% of snaps and plays 1% has halved his
workload, and that means nothing at all.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from chalktalk.definitions.signals.base import (
    AttrRef,
    CompileCtx,
    Compiled,
    Signal,
    ValidationCtx,
)
from chalktalk.definitions.signals.rule import _SQL_OP
from chalktalk.definitions.spec import Definition, InvalidDefinition
from chalktalk.features.catalog import UnknownAttribute
from chalktalk.spec_types import SELF_NS

Kind = Literal["ratio", "diff"]
Op = Literal["=", "!=", "<", "<=", ">", ">="]

NUMERIC = {"int", "float"}

_ENGLISH_OP = {"=": "is", "!=": "is not", "<": "<", "<=": "≤", ">": ">", ">=": "≥"}


class DeltaParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attr: str
    baseline: str
    kind: Kind = "ratio"
    op: Op = "<="
    value: float
    min_baseline: float | None = None


def _ref(name: str) -> AttrRef:
    namespace, _, column = name.rpartition(".")
    return AttrRef(namespace or SELF_NS, column)


class DeltaSignal(Signal):
    id = "delta"
    Params = DeltaParams

    def validate(self, d: Definition, ctx: ValidationCtx) -> None:
        params = self.parse(d)
        for field in ("attr", "baseline"):
            name = getattr(params, field)
            try:
                attribute = ctx.attribute(_ref(name))
            except UnknownAttribute as exc:
                raise InvalidDefinition(
                    str(exc), name=d.name, field=field, did_you_mean=exc.did_you_mean
                ) from exc
            if attribute.type not in NUMERIC:
                raise InvalidDefinition(
                    f"{name} is {attribute.type}; a delta needs numbers on both sides",
                    name=d.name,
                    field=field,
                )
        if params.kind == "ratio" and params.min_baseline is None:
            # Not fatal, but a ratio against an unbounded baseline is a trap.
            pass

    def compile(self, d: Definition, ctx: CompileCtx) -> Compiled:
        params = self.parse(d)
        attr_ref, base_ref = _ref(params.attr), _ref(params.baseline)
        left = f'{{{attr_ref.namespace}}}."{attr_ref.name}"'
        right = f'{{{base_ref.namespace}}}."{base_ref.name}"'
        op = _SQL_OP[params.op]

        # NULL on either side means "unknown", which must not match.
        conditions = [f"{left} IS NOT NULL", f"{right} IS NOT NULL"]
        values: list[float] = []
        if params.min_baseline is not None:
            conditions.append(f"{right} > ?")
            values.append(params.min_baseline)
        if params.kind == "ratio":
            conditions.append(f"{left} {op} ? * {right}")
        else:
            conditions.append(f"{left} - {right} {op} ?")
        values.append(params.value)

        return Compiled(
            f"({' AND '.join(conditions)})",
            params=values,
            namespaces_used={attr_ref.namespace, base_ref.namespace},
        )

    def explain(self, d: Definition, ctx: ValidationCtx) -> str:
        params = self.parse(d)
        comparison = _ENGLISH_OP[params.op]
        if params.kind == "ratio":
            text = f"{params.attr} {comparison} {params.value:g} × {params.baseline}"
        else:
            text = f"{params.attr} minus {params.baseline} {comparison} {params.value:g}"
        if params.min_baseline is not None:
            text += f" (only where {params.baseline} > {params.min_baseline:g})"
        return text

    def requires(self, d: Definition, ctx: ValidationCtx) -> list[AttrRef]:
        params = self.parse(d)
        return [_ref(params.attr), _ref(params.baseline)]
