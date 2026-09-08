"""The definitions store: a directory of JSON files, one per definition.

This is the user's accumulated work and the only thing in the system that is not
disposable. It lives *outside* the DuckDB file on purpose — losing it on a data
refresh would be the worst bug in the product — and it is JSON on disk rather
than a database so that it diffs, edits by hand, and gets history and backup
free from `git init` (D9).

Every definition is revalidated against the current schema on load. One that no
longer compiles is quarantined with a reason and listed loudly, but never
crashes the server (D10): a broken definition must fail its own query, not
everyone else's.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from chalktalk.coverage import SeasonRange
from chalktalk.definitions.signals import SIGNALS, CompileCtx, ValidationCtx
from chalktalk.definitions.spec import (
    Definition,
    DefinitionIn,
    DefinitionSummary,
    InvalidDefinition,
    Provenance,
    _now,
)
from chalktalk.entities import EntityMismatch

log = logging.getLogger(__name__)

HISTORY_DIR = ".history"


@dataclass
class LoadReport:
    loaded: int = 0
    broken: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.broken


@dataclass
class ImportReport:
    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


class DefinitionStore:
    def __init__(self, directory: Path, ctx_factory: Callable[[str], ValidationCtx]) -> None:
        self.directory = directory
        self._ctx_factory = ctx_factory
        self._by_name: dict[str, Definition] = {}
        self._by_alias: dict[str, str] = {}
        self.broken: dict[str, str] = {}
        self._mtime: float | None = None

    # ── reading ───────────────────────────────────────────────────────────────

    def _files(self) -> Iterable[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(p for p in self.directory.glob("*.json") if p.is_file())

    def load(self) -> LoadReport:
        """Parse and validate every definition. Never raises."""
        self._by_name.clear()
        self._by_alias.clear()
        self.broken.clear()
        report = LoadReport()

        parsed: list[Definition] = []
        for path in self._files():
            try:
                definition = Definition.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, ValueError, OSError) as exc:
                self.broken[path.stem] = _one_line(exc)
                continue
            if definition.name != path.stem:
                self.broken[path.stem] = (
                    f"file is named {path.stem}.json but the definition is {definition.name!r}"
                )
                continue
            parsed.append(definition)

        # Register everything first, so composites can see their terms, then
        # validate: a term that is itself broken is reported on its own row.
        for definition in parsed:
            self._by_name[definition.name] = definition
            for alias in definition.aliases:
                self._by_alias[alias] = definition.name

        # Validate to a fixed point: a composite whose term turns out to be
        # broken is broken too, and the files are read in alphabetical order, so
        # one pass would let it through when the term sorts after it.
        while True:
            newly_broken = {}
            for definition in list(self._by_name.values()):
                try:
                    self.validate(definition)
                except Exception as exc:  # noqa: BLE001 — any failure quarantines
                    newly_broken[definition.name] = _one_line(exc)
            if not newly_broken:
                break
            self.broken.update(newly_broken)
            for name in newly_broken:
                self._by_name.pop(name, None)
        self._by_alias = {
            alias: name for alias, name in self._by_alias.items() if name in self._by_name
        }

        report.loaded = len(self._by_name)
        report.broken = dict(self.broken)
        self._mtime = self._directory_mtime()

        if self.broken:
            log.warning(
                "%s definition(s) quarantined and unusable until fixed:\n%s",
                len(self.broken),
                "\n".join(f"  {n}: {why}" for n, why in sorted(self.broken.items())),
            )
        return report

    def _directory_mtime(self) -> float | None:
        if not self.directory.is_dir():
            return None
        times = [self.directory.stat().st_mtime]
        times.extend(p.stat().st_mtime for p in self._files())
        return max(times)

    def maybe_reload(self) -> bool:
        """Reload if anything on disk changed. The user may edit files by hand."""
        if self._directory_mtime() != self._mtime:
            self.load()
            return True
        return False

    def get(self, name_or_alias: str) -> Definition | None:
        if name_or_alias in self._by_name:
            return self._by_name[name_or_alias]
        target = self._by_alias.get(name_or_alias)
        return self._by_name.get(target) if target else None

    def list(self, include_broken: bool = True) -> list[DefinitionSummary]:
        summaries = [DefinitionSummary.of(d) for d in self._by_name.values()]
        if include_broken:
            for name, why in self.broken.items():
                stored = self._read_raw(name)
                summaries.append(
                    DefinitionSummary.of(stored, broken=why)
                    if stored
                    else DefinitionSummary(
                        name=name,
                        aliases=[],
                        entity="?",
                        basis=None,
                        signal="?",
                        description="",
                        source="user",
                        version=0,
                        broken=why,
                    )
                )
        return sorted(summaries, key=lambda s: s.name)

    def _read_raw(self, name: str) -> Definition | None:
        """A quarantined definition still has a file worth showing."""
        path = self.directory / f"{name}.json"
        try:
            return Definition.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — it is quarantined precisely because it may not parse
            return None

    # ── validation ────────────────────────────────────────────────────────────

    def validate(self, definition: Definition) -> None:
        """Everything that must hold before a definition can be trusted."""
        signal = SIGNALS.get(definition.signal)
        if signal is None:
            raise InvalidDefinition(
                f"unknown signal {definition.signal!r}; "
                f"expected one of {', '.join(sorted(SIGNALS))}",
                name=definition.name,
                field="signal",
                did_you_mean=sorted(SIGNALS),
            )
        ctx = self._ctx_factory(definition.entity)
        signal.validate(definition, ctx)
        # A dry compile catches anything validation alone would miss.
        signal.compile(definition, CompileCtx(**vars(ctx)))

    def coverage_of(self, name: str) -> SeasonRange | None:
        definition = self.get(name)
        if definition is None:
            return None
        signal = SIGNALS[definition.signal]
        ctx = self._ctx_factory(definition.entity)
        refs = signal.requires(definition, ctx)
        table = _table_of(definition.entity)
        return ctx.coverage.intersect((table, ref.name) for ref in refs if ref.namespace == "self")

    # ── writing ───────────────────────────────────────────────────────────────

    def save(
        self,
        incoming: DefinitionIn,
        *,
        overwrite: bool = False,
        copied_from: str | None = None,
    ) -> Definition:
        if copied_from is not None:
            source = self.get(copied_from)
            if source is None:
                raise InvalidDefinition(
                    f"cannot copy {copied_from!r}: no such definition",
                    name=incoming.name,
                    field="copied_from",
                )
            incoming = incoming.model_copy(
                update={
                    "entity": source.entity,
                    "basis": source.basis,
                    "signal": source.signal,
                    "params": source.params,
                    "description": incoming.description or source.description,
                }
            )

        existing = self._by_name.get(incoming.name)
        if (existing or incoming.name in self.broken) and not overwrite:
            raise InvalidDefinition(
                f"{incoming.name!r} already exists; pass overwrite to replace it",
                name=incoming.name,
            )
        self._check_names_are_free(incoming)

        definition = Definition(
            **incoming.model_dump(),
            provenance=Provenance(
                source="user",
                created_at=existing.provenance.created_at if existing else _now(),
                copied_from=copied_from,
            ),
            version=(existing.version + 1) if existing else 1,
        )
        self.validate(definition)

        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{definition.name}.json"
        if path.exists():
            self._archive(path, existing.version if existing else 0)

        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(definition.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

        self.load()
        return definition

    def _check_names_are_free(self, incoming: DefinitionIn) -> None:
        for candidate in [incoming.name, *incoming.aliases]:
            owner = self.get(candidate)
            if owner is not None and owner.name != incoming.name:
                raise InvalidDefinition(
                    f"{candidate!r} is already used by {owner.name!r}",
                    name=incoming.name,
                    field="aliases" if candidate != incoming.name else "name",
                )

    def _archive(self, path: Path, version: int) -> None:
        history = self.directory / HISTORY_DIR / path.stem
        history.mkdir(parents=True, exist_ok=True)
        path.replace(history / f"v{version}.json")

    def delete(self, name: str) -> None:
        path = self.directory / f"{name}.json"
        if not path.exists():
            raise InvalidDefinition(f"no such definition {name!r}", name=name)
        definition = self._by_name.get(name)
        self._archive(path, definition.version if definition else 0)
        self.load()

    # ── moving definitions around (D3) ────────────────────────────────────────

    def export(self, path: Path) -> int:
        path.mkdir(parents=True, exist_ok=True)
        count = 0
        for definition in self._by_name.values():
            target = path / f"{definition.name}.json"
            target.write_text(definition.model_dump_json(indent=2) + "\n", encoding="utf-8")
            count += 1
        return count

    def import_(
        self, path: Path, *, source: str = "imported", overwrite: bool = False
    ) -> ImportReport:
        report = ImportReport()
        files = sorted(path.glob("*.json")) if path.is_dir() else [path]
        for file in files:
            try:
                incoming = Definition.model_validate_json(file.read_text(encoding="utf-8"))
            except (ValidationError, ValueError, OSError) as exc:
                report.failed[file.stem] = _one_line(exc)
                continue
            if self.get(incoming.name) is not None and not overwrite:
                report.skipped.append(incoming.name)
                continue
            try:
                saved = self.save(
                    DefinitionIn(**incoming.model_dump(include=set(DefinitionIn.model_fields))),
                    overwrite=overwrite,
                )
            except (InvalidDefinition, EntityMismatch) as exc:
                report.failed[incoming.name] = _one_line(exc)
                continue
            stored = self.directory / f"{saved.name}.json"
            marked = saved.model_copy(
                update={"provenance": saved.provenance.model_copy(update={"source": source})}
            )
            stored.write_text(marked.model_dump_json(indent=2) + "\n", encoding="utf-8")
            report.added.append(saved.name)
        self.load()
        return report


def _table_of(entity: str) -> str:
    from chalktalk.entities import ENTITIES

    return ENTITIES[entity].table


def _one_line(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0][:300] if text else exc.__class__.__name__
