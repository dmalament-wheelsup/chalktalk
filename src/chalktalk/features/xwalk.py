"""`player_id_xwalk` — PFR id to gsis_id (D6).

Snap counts are the backbone of everything player-shaped here, and they are
keyed by Pro Football Reference id while the rest of nflverse is keyed by
`gsis_id`. `players` resolves ~99.8% of them; `rosters_weekly` fills a few of
the rest. What still does not resolve is kept under a `pfr:` key rather than
dropped, so a player never silently disappears from an answer.
"""

from __future__ import annotations

import logging

import duckdb

from chalktalk.config import Settings

log = logging.getLogger(__name__)

#: PFR's position vocabulary is granular and, in ~600 rows, compound
#: (`C/G`, `DE/L`, `G/OT`). Compounds are resolved on the first token, so this
#: maps the 28 base tokens actually present in `snap_counts`, not the 48 raw
#: values. Only used when `players` has no row for the id.
POSITION_GROUP: dict[str, str] = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "HB": "RB",
    "WR": "WR",
    "TE": "TE",
    "T": "OL",
    "OT": "OL",
    "G": "OL",
    "OG": "OL",
    "C": "OL",
    "OL": "OL",
    "DE": "DL",
    "DT": "DL",
    "NT": "DL",
    "DL": "DL",
    "LB": "LB",
    "OLB": "LB",
    "ILB": "LB",
    "MLB": "LB",
    "CB": "DB",
    "FS": "DB",
    "SS": "DB",
    "S": "DB",
    "DB": "DB",
    "K": "SPEC",
    "P": "SPEC",
    "LS": "SPEC",
}


def _position_group_case(column: str) -> str:
    """SQL mapping the first token of a PFR position to a position group."""
    arms = "\n".join(
        f"        WHEN base = '{pfr}' THEN '{group}'" for pfr, group in POSITION_GROUP.items()
    )
    return f"""
    (SELECT CASE
{arms}
        ELSE NULL END
     FROM (SELECT split_part(upper(trim({column})), '/', 1) AS base))
    """


_SQL = f"""
CREATE OR REPLACE TABLE player_id_xwalk AS
WITH from_players AS (
    SELECT pfr_id, gsis_id, display_name, position, position_group, 'players' AS source
    FROM players
    WHERE pfr_id IS NOT NULL AND gsis_id IS NOT NULL
), from_rosters AS (
    SELECT DISTINCT ON (r.pfr_id)
           r.pfr_id, r.gsis_id, r.full_name AS display_name, r.position,
           {_position_group_case("r.position")} AS position_group,
           'rosters_weekly' AS source
    FROM rosters_weekly r
    WHERE r.pfr_id IS NOT NULL
      AND r.gsis_id IS NOT NULL
      AND r.season >= ?
      AND r.pfr_id NOT IN (SELECT pfr_id FROM from_players)
    ORDER BY r.pfr_id, r.season DESC, r.week DESC
)
SELECT * FROM from_players
UNION ALL
SELECT * FROM from_rosters
"""


def build(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    conn.execute(_SQL, [settings.season_floor - settings.baseline_lookback])

    total, resolved = conn.execute(
        """
        SELECT count(*), count(x.gsis_id)
        FROM snap_counts s
        LEFT JOIN player_id_xwalk x ON x.pfr_id = s.pfr_player_id
        WHERE s.season >= ?
        """,
        [settings.season_floor],
    ).fetchone()
    rate = resolved / total if total else 0.0
    unresolved = total - resolved
    log.info(
        "player_id_xwalk: %s ids; %.2f%% of snap_counts rows resolve (%s unresolved)",
        conn.execute("SELECT count(*) FROM player_id_xwalk").fetchone()[0],
        rate * 100,
        f"{unresolved:,}",
    )
    if unresolved:
        worst = conn.execute(
            """
            SELECT s.player, s.position, min(s.season), max(s.season), count(*) AS n
            FROM snap_counts s
            LEFT JOIN player_id_xwalk x ON x.pfr_id = s.pfr_player_id
            WHERE s.season >= ? AND x.gsis_id IS NULL
            GROUP BY 1, 2 ORDER BY n DESC LIMIT 5
            """,
            [settings.season_floor],
        ).fetchall()
        log.info("player_id_xwalk: most-affected unresolved players: %s", worst)
