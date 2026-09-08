"""`game_ctx` — the `game` entity: one row per game, with context.

Everything a question about a *game* might lean on: when it was played, the
result, the betting line, the weather, and a few play-count totals from `pbp`.

The era-neutral columns matter more than they look (D24). `is_final_reg_week`
is the "rest week" a question about rested starters actually means, and it is
week 17 through 2020 and week 18 from 2021 — read from `season_status`, never
written down.
"""

from __future__ import annotations

import duckdb

from chalktalk.config import Settings
from chalktalk.features import team_abbr

# nflverse `spread_line` is home-relative and **positive means the home team is
# favoured** — the negation of the conventional point spread. Verified
# 2026-09-07: Super Bowl LVIII (home KC, away SF) is -1.5 with SF favoured, and
# over 3,407 regular-season games home-favoured games are won by the home team
# 67.3% of the time (average margin +5.77) against 34.5% when away-favoured.
_SQL_TEMPLATE = """
CREATE OR REPLACE TABLE game_ctx AS
WITH pbp_agg AS (
    SELECT game_id,
           count(*) FILTER (play_type IN ('pass','run','punt','field_goal',
                                          'kickoff','extra_point','qb_kneel','qb_spike'))
               AS plays_total,
           count(*) FILTER (play_type = 'pass') AS pass_plays,
           count(*) FILTER (play_type = 'run')  AS rush_plays
    FROM pbp GROUP BY game_id
)
SELECT
    s.game_id,
    s.season,
    s.week,
    s.game_type,
    s.game_type <> 'REG'                                    AS is_postseason,
    ss.reg_weeks,
    s.game_type = 'REG' AND s.week = ss.reg_weeks           AS is_final_reg_week,
    CASE WHEN s.game_type = 'REG' THEN ss.reg_weeks - s.week END AS weeks_remaining_reg,
    s.gameday::DATE                                         AS gameday,
    s.weekday,
    s.gametime,
    try_cast(split_part(s.gametime, ':', 1) AS INTEGER)     AS kickoff_hour,
    {home_team}                                             AS home_team,
    {away_team}                                             AS away_team,
    s.home_score,
    s.away_score,
    s.result,
    abs(s.result)                                           AS margin_abs,
    s.total,
    coalesce(s.overtime = 1, FALSE)                         AS overtime,
    s.home_score IS NOT NULL                                AS is_final,
    s.spread_line,
    s.total_line,
    coalesce(s.div_game = 1, FALSE)                         AS div_game,
    s.roof,
    s.surface,
    s.temp,
    s.wind,
    s.home_rest,
    s.away_rest,
    s.home_qb_id,
    s.home_qb_name,
    s.away_qb_id,
    s.away_qb_name,
    s.home_coach,
    s.away_coach,
    s.referee,
    s.stadium,
    coalesce(p.plays_total, 0)::INTEGER                     AS plays_total,
    coalesce(p.pass_plays, 0)::INTEGER                      AS pass_plays,
    coalesce(p.rush_plays, 0)::INTEGER                      AS rush_plays
FROM schedules s
LEFT JOIN season_status ss ON ss.season = s.season
LEFT JOIN pbp_agg p        ON p.game_id = s.game_id
WHERE s.season >= ?
"""


def build(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    sql = _SQL_TEMPLATE.format(
        home_team=team_abbr.sql("s.home_team"),
        away_team=team_abbr.sql("s.away_team"),
    )
    conn.execute(sql, [settings.season_floor])
