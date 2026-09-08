"""`team_season` — the `team_season` entity: one row per team per season.

Rate columns exist because totals are not comparable across eras: a 16-game
season and a 17-game season produce different totals from identical play (D24).
Anything a definition wants to threshold should be a rate or a share.

Regular season only for the rate stats; the postseason columns say so by name.
"""

from __future__ import annotations

import duckdb

from chalktalk.config import Settings

_SQL = """
CREATE OR REPLACE TABLE team_season AS
WITH reg AS (
    SELECT season, team,
           count(*)                          AS games,
           count(*) FILTER (won)             AS wins,
           count(*) FILTER (lost)            AS losses,
           count(*) FILTER (tied)            AS ties,
           sum(points_for)                   AS points_for,
           sum(points_against)               AS points_against,
           sum(off_epa_per_play * off_plays) AS off_epa_weighted,
           sum(off_plays)                    AS off_plays,
           sum(def_epa_per_play * def_plays) AS def_epa_weighted,
           sum(def_plays)                    AS def_plays,
           sum(pass_attempts)                AS pass_attempts
    FROM team_game
    WHERE game_type = 'REG' AND is_final
    GROUP BY season, team
),
post AS (
    SELECT season, team,
           count(*)              AS playoff_games,
           count(*) FILTER (won) AS playoff_wins
    FROM team_game
    WHERE is_postseason AND is_final
    GROUP BY season, team
)
SELECT
    r.season,
    r.team,
    r.games::INTEGER                              AS games,
    ss.games_per_team,
    r.wins::INTEGER AS wins, r.losses::INTEGER AS losses, r.ties::INTEGER AS ties,
    (r.wins + 0.5 * r.ties) / nullif(r.games, 0)  AS win_pct,
    r.points_for::INTEGER                         AS points_for,
    r.points_against::INTEGER                     AS points_against,
    (r.points_for - r.points_against)::INTEGER    AS point_diff,
    r.points_for::DOUBLE / nullif(r.games, 0)     AS points_for_per_game,
    r.points_against::DOUBLE / nullif(r.games, 0) AS points_against_per_game,
    coalesce(p.playoff_games, 0) > 0              AS made_playoffs,
    coalesce(p.playoff_wins, 0)::INTEGER          AS playoff_wins,
    r.off_epa_weighted / nullif(r.off_plays, 0)   AS off_epa_per_play,
    r.def_epa_weighted / nullif(r.def_plays, 0)   AS def_epa_per_play,
    r.pass_attempts::DOUBLE / nullif(r.off_plays, 0) AS pass_rate,
    t.team_division                               AS division,
    t.team_conf                                   AS conference
FROM reg r
LEFT JOIN post p          ON p.season = r.season AND p.team = r.team
LEFT JOIN season_status ss ON ss.season = r.season
-- `teams` lists both spellings for relocated franchises (LA and LAR), so one
-- row per abbreviation keeps the join from fanning out. The historical
-- spellings simply never match, since team_game.team is normalized.
LEFT JOIN (SELECT DISTINCT ON (team_abbr) team_abbr, team_division, team_conf
           FROM teams) t ON t.team_abbr = r.team
"""


def build(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    conn.execute(_SQL)
