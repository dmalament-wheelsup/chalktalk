"""`propose` — what a fuzzy word could mean, grounded in what is computable.

Deterministic and model-free. The server has no model in it: this returns
candidates, and a person or a calling model picks. Every suggestion is a
ready-to-save definition with its coverage attached, so nothing is offered that
the data cannot actually answer.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from chalktalk.coverage import SeasonRange
from chalktalk.definitions.signals import SIGNALS
from chalktalk.definitions.spec import DefinitionIn, DefinitionSummary
from chalktalk.definitions.vocabulary import Family, families_for
from chalktalk.entities import ENTITIES
from chalktalk.features.catalog import ATTRIBUTES, Attribute

#: How close a name has to be before it counts as "you may already have this".
MATCH_RATIO = 0.8

#: Families where a larger number means more of the thing, so "top decile" is a
#: sensible automatic suggestion. `draft` is deliberately absent: round 1 is the
#: best round, so a top-percentile suggestion on draft_round would be backwards.
#: D2 ships `star_by_draft` as a rule (`draft_round = 1`) for exactly that reason.
ASCENDING_FAMILIES = frozenset({"snaps", "production", "contract", "epa", "result"})


@dataclass
class Suggestion:
    definition: DefinitionIn
    explanation: str
    coverage: SeasonRange | None
    why: str


@dataclass
class Proposal:
    term: str
    matches: list[DefinitionSummary] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    related_attributes: list[Attribute] = field(default_factory=list)
    not_computable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    signals: dict[str, dict] = field(default_factory=dict)


def _tokens(text: str) -> list[str]:
    return [t for t in "".join(c if c.isalnum() else " " for c in text.lower()).split() if t]


def _existing_matches(term: str, store) -> list[DefinitionSummary]:
    summaries = store.list(include_broken=False)
    scored = []
    for summary in summaries:
        best = max(
            difflib.SequenceMatcher(None, term.lower(), name.lower()).ratio()
            for name in [summary.name, *summary.aliases]
        )
        if best >= MATCH_RATIO:
            scored.append((best, summary))
    return [s for _, s in sorted(scored, key=lambda x: -x[0])]


def _rank_attributes(
    tokens: list[str], families: list[Family], entity: str | None
) -> list[Attribute]:
    """Catalog search: named by a family first, then by word overlap."""
    named: list[str] = []
    for family in families:
        named.extend(family.suggest_attrs)

    scored: list[tuple[float, Attribute]] = []
    for (attr_entity, name), attribute in ATTRIBUTES.items():
        if entity and attr_entity != entity:
            continue
        score = 0.0
        if name in named:
            score += 10 - named.index(name) * 0.1
        haystack = f"{name} {attribute.description}".lower()
        for token in tokens:
            if token == name:
                score += 5
            elif token in name.split("_"):
                score += 2
            elif token in haystack:
                score += 0.5
        if score:
            scored.append((score, attribute))
    scored.sort(key=lambda s: (-s[0], s[1].entity, s[1].name))
    return [a for _, a in scored[:15]]


def _suggestions_from_families(
    families: list[Family], store, entity: str | None
) -> list[Suggestion]:
    """Shipped definitions a family names, when they exist in this store."""
    out: list[Suggestion] = []
    for family in families:
        for name in family.suggest_definitions:
            existing = store.get(name)
            if existing is None:
                continue
            if entity and existing.entity != entity and existing.entity not in ENTITIES:
                continue
            explanation = ""
            try:
                explanation = SIGNALS[existing.signal].explain(
                    existing, store._ctx_factory(existing.entity)
                )
            except Exception:  # noqa: BLE001 — a suggestion is not worth failing over
                explanation = existing.description
            out.append(
                Suggestion(
                    definition=DefinitionIn(
                        **existing.model_dump(include=set(DefinitionIn.model_fields))
                    ),
                    explanation=explanation,
                    coverage=store.coverage_of(existing.name),
                    why=f"shipped for the {family.id!r} family",
                )
            )
    return out


def _percentile_suggestion(term: str, attribute: Attribute, store) -> Suggestion | None:
    """The commonest shape a fuzzy superlative takes: top decile of something."""
    if attribute.type not in ("int", "float"):
        return None
    if attribute.entity not in ("player_season", "team_season"):
        return None
    if attribute.family not in ASCENDING_FAMILIES:
        return None
    cohort = ["season"]
    if ("player_season", "position_group") in ATTRIBUTES and attribute.entity == "player_season":
        cohort.append("position_group")
    name = f"{_slug(term)}_by_{attribute.name}"[:64]
    candidate = DefinitionIn(
        name=name,
        entity=attribute.entity,
        signal="percentile",
        params={
            "attr": attribute.name,
            "cohort": cohort,
            "pctile": 90,
            "direction": "top",
        },
        description=f"Top 10% of {attribute.name} within {' and '.join(cohort)}.",
    )
    return Suggestion(
        definition=candidate,
        explanation=(
            f"{attribute.name} at or above the 90th percentile within {' and '.join(cohort)}"
        ),
        coverage=None,
        why=f"{attribute.name} is a number, so it can be ranked within a cohort",
    )


def _slug(term: str) -> str:
    tokens = _tokens(term)
    slug = "_".join(tokens) or "term"
    if not slug[0].isalpha():
        slug = f"t_{slug}"
    return slug


def propose(
    term: str,
    context: str | None = None,
    entity: str | None = None,
    *,
    store,
    coverage=None,
) -> Proposal:
    """Candidates for what ``term`` might mean. No model, no guessing."""
    text = f"{term} {context or ''}".strip()
    families = families_for(text)
    tokens = _tokens(text)

    proposal = Proposal(
        term=term,
        matches=_existing_matches(term, store),
        families=[f.id for f in families],
        not_computable=[f.not_computable for f in families if f.not_computable],
        notes=[f.note for f in families if f.note],
        signals={sid: signal.Params.model_json_schema() for sid, signal in SIGNALS.items()},
    )

    proposal.related_attributes = _rank_attributes(tokens, families, entity)
    proposal.suggestions = _suggestions_from_families(families, store, entity)

    # If nothing is shipped for this word, offer the shape it most likely takes —
    # but only from attributes the vocabulary actually associates with it.
    # Inventing "injured_by_attempts_per_game" from an unrelated numeric column
    # would be worse than offering nothing.
    if not proposal.suggestions and not proposal.not_computable:
        named = {a for family in families for a in family.suggest_attrs}
        strong = {t for t in tokens}
        for attribute in proposal.related_attributes:
            if attribute.name not in named and attribute.name not in strong:
                continue
            suggestion = _percentile_suggestion(term, attribute, store)
            if suggestion is not None:
                proposal.suggestions.append(suggestion)
            if len(proposal.suggestions) >= 3:
                break

    return proposal


def as_dict(proposal: Proposal) -> dict[str, Any]:
    """The MCP-facing shape (phase 8 returns this verbatim)."""
    return {
        "term": proposal.term,
        "matches": [m.model_dump() for m in proposal.matches],
        "families": proposal.families,
        "suggestions": [
            {
                "definition": s.definition.model_dump(),
                "explanation": s.explanation,
                "coverage": (
                    {"first": s.coverage.first, "last": s.coverage.last} if s.coverage else None
                ),
                "why": s.why,
            }
            for s in proposal.suggestions
        ],
        "related_attributes": [
            {
                "entity": a.entity,
                "name": a.name,
                "type": a.type,
                "family": a.family,
                "description": a.description,
            }
            for a in proposal.related_attributes
        ],
        "not_computable": proposal.not_computable,
        "notes": proposal.notes,
        "signals": proposal.signals,
    }
