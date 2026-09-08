"""The five signals. The set is closed (D22).

A new football concept is a definition, never a new signal. If something cannot
be said with these five, the feature layer is missing an attribute.
"""

from __future__ import annotations

from chalktalk.definitions.signals.base import (
    CTE,
    AttrRef,
    CompileCtx,
    Compiled,
    Signal,
    ValidationCtx,
)
from chalktalk.definitions.signals.rule import RuleSignal

SIGNALS: dict[str, Signal] = {
    RuleSignal.id: RuleSignal(),
}

__all__ = [
    "CTE",
    "SIGNALS",
    "AttrRef",
    "CompileCtx",
    "Compiled",
    "Signal",
    "ValidationCtx",
]
