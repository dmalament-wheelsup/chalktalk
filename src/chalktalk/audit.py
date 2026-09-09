"""One JSON line per query, and what to do with them.

The log is not for debugging. `raw_sql` is the escape hatch for questions the
tool surface cannot yet answer, so a query that keeps being written by hand is
evidence that something should become a first-class attribute or definition.
`chalktalk logs summary` is the roadmap.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chalktalk.config import Settings
from chalktalk.paths import audit_log, logs_dir

log = logging.getLogger(__name__)

_NUMBER = re.compile(r"\b\d+(\.\d+)?\b")
_STRING = re.compile(r"'(?:[^']|'')*'")
_SPACE = re.compile(r"\s+")


def fingerprint(sql: str) -> str:
    """Collapse a statement to its shape, so repeats of it group together."""
    text = _STRING.sub("'S'", sql)
    text = _NUMBER.sub("N", text)
    return _SPACE.sub(" ", text).strip().lower()


def record(settings: Settings, entry: dict[str, Any]) -> None:
    """Append one line. Never raises: a failed log must not fail a query."""
    entry = {"ts": datetime.now(UTC).replace(microsecond=0).isoformat(), **entry}
    try:
        logs_dir(settings).mkdir(parents=True, exist_ok=True)
        with audit_log(settings).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
    except OSError as exc:
        log.warning("could not write the audit log: %s", exc)


def read(settings: Settings, since: timedelta | None = None) -> list[dict[str, Any]]:
    path = audit_log(settings)
    if not path.is_file():
        return []
    cutoff = (datetime.now(UTC) - since).isoformat() if since else None
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cutoff and entry.get("ts", "") < cutoff:
            continue
        entries.append(entry)
    return entries


#: `Not` stores its clause under `not_` when a plan is dumped without aliases,
#: but a plan that arrived as JSON says `not`. Accept both.
_NOT_KEYS = ("not", "not_")


def _walk_clause(clause: Any, terms: Counter, attributes: Counter) -> None:
    if not isinstance(clause, dict):
        return
    if "term" in clause:
        terms[clause["term"]] += 1
        return
    if "attr" in clause:
        attributes[clause["attr"]] += 1
        return
    for key in _NOT_KEYS:
        if key in clause:
            _walk_clause(clause[key], terms, attributes)
            return
    for key in ("any_of", "all_of"):
        for inner in clause.get(key) or []:
            _walk_clause(inner, terms, attributes)


def plan_usage(plan: dict[str, Any]) -> tuple[Counter, Counter]:
    """(terms, attributes) a plan names — in filters, grouping and metrics alike."""
    terms: Counter = Counter()
    attributes: Counter = Counter()

    for clause in plan.get("where") or []:
        _walk_clause(clause, terms, attributes)

    for key in plan.get("group_by") or []:
        if isinstance(key, dict) and "term" in key:
            terms[key["term"]] += 1
        elif isinstance(key, str):
            attributes[key] += 1

    for metric in plan.get("metrics") or []:
        of = metric.get("of") if isinstance(metric, dict) else None
        if isinstance(of, dict) and "term" in of:
            terms[of["term"]] += 1
        elif isinstance(of, str):
            attributes[of] += 1

    return terms, attributes


@dataclass
class TermUsage:
    """Which words a person actually reaches for, and which they keep spelling out."""

    terms: Counter
    definitions: Counter
    attributes: Counter
    unresolved: Counter
    queries: int
    with_terms: int


def term_usage(settings: Settings, since: timedelta | None = None) -> TermUsage:
    terms: Counter = Counter()
    definitions: Counter = Counter()
    attributes: Counter = Counter()
    unresolved: Counter = Counter()
    queries = 0
    with_terms = 0

    for entry in read(settings, since):
        if entry.get("tool") not in ("query", "explain_query"):
            continue
        queries += 1
        plan_terms, plan_attributes = plan_usage(entry.get("plan") or {})
        terms.update(plan_terms)
        attributes.update(plan_attributes)
        if plan_terms:
            with_terms += 1
        for used in entry.get("definitions_used") or []:
            definitions[used["name"]] += 1
        if entry.get("error") == "unresolved_term":
            unresolved.update(plan_terms)

    return TermUsage(
        terms=terms,
        definitions=definitions,
        attributes=attributes,
        unresolved=unresolved,
        queries=queries,
        with_terms=with_terms,
    )


@dataclass
class Summary:
    calls: Counter
    errors: Counter
    raw_shapes: list[tuple[str, int, str]]  # fingerprint, count, last seen
    total: int


def summarize(settings: Settings, since: timedelta | None = None, top: int = 20) -> Summary:
    entries = read(settings, since)
    calls: Counter = Counter(e.get("tool", "?") for e in entries)
    errors: Counter = Counter(e["error"] for e in entries if e.get("error"))

    shapes: Counter = Counter()
    last_seen: dict[str, str] = {}
    for entry in entries:
        if entry.get("tool") != "raw_sql" or not entry.get("sql"):
            continue
        shape = fingerprint(entry["sql"])
        shapes[shape] += 1
        last_seen[shape] = entry.get("ts", "")

    return Summary(
        calls=calls,
        errors=errors,
        raw_shapes=[(s, n, last_seen.get(s, "")) for s, n in shapes.most_common(top)],
        total=len(entries),
    )


def parse_since(text: str) -> timedelta:
    """`30d`, `12h`, `90m`."""
    match = re.fullmatch(r"(\d+)([dhm])", text.strip().lower())
    if not match:
        raise ValueError(f"expected something like 30d, 12h or 90m, got {text!r}")
    amount, unit = int(match.group(1)), match.group(2)
    return {
        "d": timedelta(days=amount),
        "h": timedelta(hours=amount),
        "m": timedelta(minutes=amount),
    }[unit]


def path_for(settings: Settings) -> Path:
    return audit_log(settings)
