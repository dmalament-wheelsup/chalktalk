"""One spelling per franchise.

nflverse is not internally consistent about team abbreviations, and the
inconsistency is silent: `pbp` and `player_stats` always use the *current*
franchise code, while `schedules`, `snap_counts`, `injuries`, `participation`
and `teams` use the code that was correct *at the time*, and `rosters_weekly`
adds a set of PFR-style spellings of its own.

Joining across the two conventions does not fail — it simply matches nothing.
Before this existed, every Oakland, San Diego and St. Louis team-game had zero
offensive plays and zero EPA, because `team_game.team` said OAK and
`pbp.posteam` said LV.

The canonical spelling is the current one, which is what `pbp` uses.
"""

from __future__ import annotations

#: Alias → canonical. Derived from the data on 2026-09-07 by diffing every
#: team column against the 32 values `pbp.posteam` takes.
ALIASES: dict[str, str] = {
    # Relocations. The historical code appears in schedules, snap_counts,
    # injuries, participation and teams for the seasons before the move.
    "OAK": "LV",  # Oakland Raiders -> Las Vegas, 2020
    "SD": "LAC",  # San Diego Chargers -> Los Angeles, 2017
    "STL": "LA",  # St. Louis Rams -> Los Angeles, 2016
    "SL": "LA",  # rosters_weekly spells St. Louis this way
    "LAR": "LA",  # `teams` carries both spellings of the Rams
    # PFR-style spellings, rosters_weekly only.
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
}

#: The 32 current franchises, as `pbp` spells them.
CANONICAL = frozenset(
    {
        "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
        "DET", "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA",
        "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
        "TEN", "WAS",
    }
)  # fmt: skip


def sql(column: str) -> str:
    """A SQL expression mapping ``column`` to the canonical franchise code."""
    arms = " ".join(f"WHEN '{alias}' THEN '{code}'" for alias, code in ALIASES.items())
    return f"CASE {column} {arms} ELSE {column} END"


def canonical(abbr: str | None) -> str | None:
    return None if abbr is None else ALIASES.get(abbr, abbr)
