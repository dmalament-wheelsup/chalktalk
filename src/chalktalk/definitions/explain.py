"""Turning a definition back into English.

Every answer states which definitions produced it, and a name alone is not a
statement anyone can disagree with. `early_exit` means nothing until it reads
"played and regular and (left early or snap drop) and corroborated", with each
of those expandable in turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chalktalk.coverage import SeasonRange
from chalktalk.definitions.signals import SIGNALS, AttrRef, ValidationCtx
from chalktalk.definitions.spec import Definition


@dataclass
class Explanation:
    name: str
    text: str
    coverage: SeasonRange | None = None
    warnings: list[str] = field(default_factory=list)
    evidence: list[AttrRef] = field(default_factory=list)
    parts: list[Explanation] = field(default_factory=list)

    def render(self, indent: int = 0) -> str:
        """A readable tree, for the CLI and for a model to quote back."""
        pad = "  " * indent
        head = f"{pad}{self.name}: {self.text}"
        if self.coverage and indent == 0:
            head += f"  [{self.coverage.first}-{self.coverage.last}]"
        lines = [head]
        lines += [f"{pad}  ! {w}" for w in self.warnings]
        lines += [part.render(indent + 1) for part in self.parts]
        return "\n".join(lines)


def explain(defn: Definition, store, *, _seen: tuple[str, ...] = ()) -> Explanation:
    """A definition, its terms, and what limits it."""
    ctx: ValidationCtx = store._ctx_factory(defn.entity)
    signal = SIGNALS[defn.signal]

    warnings: list[str] = []
    try:
        text = signal.explain(defn, ctx)
    except Exception as exc:  # noqa: BLE001 — a broken definition still explains itself
        text = f"cannot be explained: {exc}"
        warnings.append("this definition is broken and will not run")

    coverage = store.coverage_of(defn.name)
    if coverage is None and defn.name in store.broken:
        warnings.append(store.broken[defn.name])

    if defn.basis == "prior_season":
        text = f"{text}, measured on the previous season"

    parts: list[Explanation] = []
    if defn.signal == "composite" and defn.name not in _seen:
        for name in defn.params.get("terms", []):
            term = store.get(name)
            if term is not None:
                parts.append(explain(term, store, _seen=(*_seen, defn.name)))

    try:
        evidence = signal.evidence(defn, ctx)
    except Exception:  # noqa: BLE001
        evidence = []

    return Explanation(
        name=defn.name,
        text=text,
        coverage=coverage,
        warnings=warnings,
        evidence=evidence,
        parts=parts,
    )
