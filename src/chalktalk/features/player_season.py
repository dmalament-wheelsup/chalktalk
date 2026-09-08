"""`player_season` — the `player_season` entity: one row per player per season.

Built from raw tables only, never from `player_game`, so `player_game` can join
it for prior-season baselines without a cycle. Regular season only unless a
column says otherwise.

Two conventions carry a lot of weight here. `snaps_unit` is offence snaps for an
offensive player and defence snaps for a defensive one, special teams excluded
(D12) — the attribute name is the decision, so nothing downstream has to ask.
And every production total has a `_per_game` twin, because a 16-game total and a
17-game total are not the same achievement (D24): a "300-carry season" is 18.75
a game in 2016 and 17.6 in 2023.
"""

from __future__ import annotations

import logging

import duckdb

from chalktalk.config import Settings
from chalktalk.features import team_abbr

log = logging.getLogger(__name__)

#: Production totals summed from `player_stats`, each of which also gets a
#: `_per_game` rate. These are the names phase 2 recorded from the loader.
PRODUCTION_STATS: dict[str, str] = {
    "attempts": "INTEGER",
    "completions": "INTEGER",
    "passing_yards": "INTEGER",
    "passing_tds": "INTEGER",
    "passing_interceptions": "INTEGER",
    "sacks_suffered": "INTEGER",
    "carries": "INTEGER",
    "rushing_yards": "INTEGER",
    "rushing_tds": "INTEGER",
    "targets": "INTEGER",
    "receptions": "INTEGER",
    "receiving_yards": "INTEGER",
    "receiving_tds": "INTEGER",
    "fantasy_points": "DOUBLE",
    "fantasy_points_ppr": "DOUBLE",
}

#: position_group -> unit. Special teams is its own unit and has no `snaps_unit`
#: (a kicker's snap share is not a meaningful number), so it stays NULL.
UNIT_OF_GROUP = {
    "QB": "offense",
    "RB": "offense",
    "WR": "offense",
    "TE": "offense",
    "OL": "offense",
    "DL": "defense",
    "LB": "defense",
    "DB": "defense",
    "SPEC": "special",
}


def _unit_case(column: str) -> str:
    arms = " ".join(f"WHEN '{group}' THEN '{unit}'" for group, unit in UNIT_OF_GROUP.items())
    return f"CASE {column} {arms} ELSE NULL END"


def _production_sums() -> str:
    # Summing an INTEGER column yields HUGEINT in DuckDB; a season total fits an
    # INTEGER comfortably and the catalog says so.
    return ",\n           ".join(
        f"sum({stat})::{sql_type} AS {stat}" for stat, sql_type in PRODUCTION_STATS.items()
    )


def _production_selects() -> str:
    lines = []
    for stat in PRODUCTION_STATS:
        lines.append(f"    prod.{stat},")
        lines.append(
            f"    prod.{stat}::DOUBLE / nullif(base.games_with_snaps, 0) AS {stat}_per_game,"
        )
    return "\n".join(lines)


_SQL_TEMPLATE = """
CREATE OR REPLACE TABLE player_season AS
WITH snaps AS (
    -- One row per player per game. PFR very occasionally repeats a player in a
    -- game; keep the row with the most snaps.
    SELECT DISTINCT ON (s.pfr_player_id, s.game_id)
           s.pfr_player_id, s.game_id, s.season, s.week, s.game_type,
           {team} AS team, s.position AS pfr_position,
           s.offense_snaps, s.defense_snaps, s.st_snaps,
           s.offense_pct, s.defense_pct, s.st_pct
    FROM snap_counts s
    WHERE s.season >= ? AND s.game_type = 'REG'
    ORDER BY s.pfr_player_id, s.game_id,
             coalesce(s.offense_snaps, 0) + coalesce(s.defense_snaps, 0)
             + coalesce(s.st_snaps, 0) DESC
),
games AS (
    SELECT sn.*,
           x.gsis_id,
           x.display_name,
           x.position_group,
           coalesce(x.gsis_id, 'pfr:' || sn.pfr_player_id) AS player_key,
           {unit} AS unit
    FROM snaps sn
    LEFT JOIN player_id_xwalk x ON x.pfr_id = sn.pfr_player_id
),
per_game AS (
    SELECT *,
           CASE unit WHEN 'offense' THEN offense_snaps
                     WHEN 'defense' THEN defense_snaps END AS snaps_unit,
           CASE unit WHEN 'offense' THEN offense_pct
                     WHEN 'defense' THEN defense_pct END  AS snap_share_unit,
           coalesce(offense_snaps, 0) + coalesce(defense_snaps, 0)
           + coalesce(st_snaps, 0)                        AS snaps_total
    FROM games
),
base AS (
    SELECT player_key,
           any_value(gsis_id)                             AS gsis_id,
           any_value(pfr_player_id)                       AS pfr_id,
           any_value(display_name)                        AS player_name,
           season,
           any_value(position_group)                      AS position_group,
           mode(pfr_position)                             AS position,
           any_value(unit)                                AS unit,
           mode(team)                                     AS team_primary,
           count(DISTINCT team)::INTEGER                  AS teams_count,
           count(*)::INTEGER                              AS games,
           count(*) FILTER (snaps_unit >= 1)::INTEGER     AS games_with_snaps,
           sum(snaps_unit)::INTEGER                       AS snaps_unit_total,
           avg(snap_share_unit) FILTER (snaps_unit >= 1)  AS snap_share_mean,
           max(snap_share_unit)                           AS snap_share_max,
           sum(snaps_total)::INTEGER                      AS snaps_total
    FROM per_game
    GROUP BY player_key, season
),
experience AS (
    SELECT gsis_id, season, max(years_exp)::INTEGER AS years_exp
    FROM rosters_weekly
    WHERE gsis_id IS NOT NULL AND season >= ?
    GROUP BY gsis_id, season
),
production AS (
    SELECT player_id AS gsis_id, season,
           {production_sums}
    FROM player_stats
    WHERE season_type = 'REG' AND season >= ?
    GROUP BY player_id, season
),
qb_starts AS (
    SELECT starting_qb_id AS gsis_id, season, count(*)::INTEGER AS qb_starts
    FROM team_game
    WHERE game_type = 'REG' AND starting_qb_id IS NOT NULL
    GROUP BY starting_qb_id, season
),
-- 2016+ only: games in which the player was on the field for his unit's first
-- snap. The data-derived notion of "started" for non-quarterbacks (D23).
first_unit AS (
    -- DISTINCT games: participation very occasionally lists a player on both
    -- sides of one play, which would otherwise count that game twice.
    SELECT pp.gsis_id, g.season, count(DISTINCT g.game_id)::INTEGER AS first_unit_play_games
    FROM player_play pp
    JOIN game_ctx g ON g.game_id = pp.game_id
    WHERE pp.unit_play_idx = 1 AND g.game_type = 'REG'
    GROUP BY pp.gsis_id, g.season
),
-- The contract in force during the season: signed on or before it and not yet
-- expired. Where a player signed more than one in a year, the largest wins.
contract AS (
    SELECT DISTINCT ON (c.gsis_id, s.season)
           c.gsis_id, s.season,
           c.year_signed AS contract_year_signed,
           c.years       AS contract_years,
           c.apy, c.apy_cap_pct, c.guaranteed
    FROM contracts c
    CROSS JOIN (SELECT DISTINCT season FROM base) s
    WHERE c.gsis_id IS NOT NULL
      AND c.year_signed <= s.season
      AND s.season < c.year_signed + coalesce(c.years, 1)
    ORDER BY c.gsis_id, s.season, c.year_signed DESC, c.apy DESC NULLS LAST
)
SELECT
    base.player_key,
    base.gsis_id,
    base.pfr_id,
    base.player_name,
    base.season,
    base.position_group,
    base.position,
    base.unit,
    base.team_primary,
    base.teams_count,
    base.games,
    base.games_with_snaps,
    base.snaps_unit_total,
    base.snaps_total,
    base.snap_share_mean,
    base.snap_share_max,
    ts.games                                           AS team_games,
    ts.games - base.games_with_snaps                   AS games_missed,
    base.games_with_snaps::DOUBLE / nullif(ts.games, 0) AS games_played_share,
    experience.years_exp,
    p.rookie_season = base.season                      AS is_rookie,
    p.draft_year,
    p.draft_round,
    p.draft_pick,
    p.draft_year IS NULL                               AS undrafted,
    contract.contract_year_signed,
    contract.contract_years,
    contract.apy,
    contract.apy_cap_pct,
    contract.guaranteed,
{production_selects}
    coalesce(qb_starts.qb_starts, 0)                   AS qb_starts,
    first_unit.first_unit_play_games
FROM base
LEFT JOIN team_season ts   ON ts.season = base.season AND ts.team = base.team_primary
LEFT JOIN experience       ON experience.gsis_id = base.gsis_id
                          AND experience.season = base.season
LEFT JOIN players p        ON p.gsis_id = base.gsis_id
LEFT JOIN production prod  ON prod.gsis_id = base.gsis_id AND prod.season = base.season
LEFT JOIN qb_starts        ON qb_starts.gsis_id = base.gsis_id
                          AND qb_starts.season = base.season
LEFT JOIN first_unit       ON first_unit.gsis_id = base.gsis_id
                          AND first_unit.season = base.season
LEFT JOIN contract         ON contract.gsis_id = base.gsis_id
                          AND contract.season = base.season
"""


def build(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    floor = settings.season_floor - settings.baseline_lookback
    sql = _SQL_TEMPLATE.format(
        team=team_abbr.sql("s.team"),
        unit=_unit_case("x.position_group"),
        production_sums=_production_sums(),
        production_selects=_production_selects(),
    )
    conn.execute(sql, [floor, floor, floor])
    rows = conn.execute("SELECT count(*) FROM player_season").fetchone()[0]
    log.info("player_season: %s rows", f"{rows:,}")
