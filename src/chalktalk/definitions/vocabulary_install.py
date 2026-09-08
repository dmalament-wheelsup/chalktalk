"""Installing the shipped vocabulary into a user's store.

The shipped definitions are ordinary definitions — same format, same signals,
same validation as anything a user writes. That is the point: if a shipped
concept needed code of its own, the feature layer would be missing an attribute
(D22). They are copied into the store rather than read from the package so that
a user can edit or replace any of them.

`star_player` is deliberately **not** shipped (D2). The three `star_by_*`
definitions are, so that the gate fires on first use of "star player" and
`propose_definition` can offer them — the user picks which one they meant.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from chalktalk.definitions.spec import Definition, DefinitionIn, InvalidDefinition

log = logging.getLogger(__name__)

PACKAGE = "chalktalk.definitions.shipped"


@dataclass
class InstallReport:
    installed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def shipped_definitions() -> list[Definition]:
    """Every definition that ships with the package, in dependency order."""
    out: list[Definition] = []
    for entry in sorted(resources.files(PACKAGE).iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".json"):
            out.append(Definition.model_validate_json(entry.read_text(encoding="utf-8")))
    return _in_dependency_order(out)


def _in_dependency_order(definitions: list[Definition]) -> list[Definition]:
    """Composites after the terms they name, so a store never sees a dangling reference."""
    by_name = {d.name: d for d in definitions}
    ordered: list[Definition] = []
    placed: set[str] = set()

    def place(definition: Definition, seen: tuple[str, ...] = ()) -> None:
        if definition.name in placed or definition.name in seen:
            return
        if definition.signal == "composite":
            for term in definition.params.get("terms", []):
                if term in by_name:
                    place(by_name[term], (*seen, definition.name))
        placed.add(definition.name)
        ordered.append(definition)

    for definition in definitions:
        place(definition)
    return ordered


def install(store, *, overwrite: bool = False) -> InstallReport:
    """Copy the shipped vocabulary into a store, leaving the user's own alone."""
    report = InstallReport()
    for definition in shipped_definitions():
        existing = store.get(definition.name)
        if existing is not None and not overwrite:
            report.kept.append(definition.name)
            continue
        incoming = DefinitionIn(**definition.model_dump(include=set(DefinitionIn.model_fields)))
        try:
            saved = store.save(incoming, overwrite=overwrite)
        except InvalidDefinition as exc:
            report.failed[definition.name] = exc.message
            continue
        _mark_shipped(store.directory / f"{saved.name}.json")
        report.installed.append(saved.name)
    store.load()
    if report.failed:
        log.warning(
            "%s shipped definition(s) could not be installed: %s",
            len(report.failed),
            "; ".join(f"{n}: {w}" for n, w in report.failed.items()),
        )
    return report


def _mark_shipped(path: Path) -> None:
    """Record where it came from, so a listing can tell shipped from authored."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["provenance"]["source"] = "shipped"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
