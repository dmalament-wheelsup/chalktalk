"""Logging setup.

stderr only, always: MCP stdio transport owns stdout, and a stray handler on it
corrupts the protocol stream.
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: int | str = logging.INFO) -> None:
    """Attach a single stderr handler to the root logger, replacing any existing."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
