"""`team_game` — the `team_game` entity: two rows per game, one per team.

Everything is stated from that team's point of view, which is the whole reason
this table exists: `margin`, `spread` and `max_lead` all flip sign between the
two rows of the same game.

`games_played_before` and `season_progress` are the era-neutral way to say "late
in the season" — comparing week numbers across the 16- and 17-game eras does not
work (D24).
"""

from __future__ import annotations

import duckdb

from chalktalk.config import Settings

# `spread` is team-relative and negative means this team is favoured, which is
# the conventional reading of a point spread and the *negation* of nflverse's
# home-relative `spread_line` (see game_ctx). Home team: -spread_line. Away
# team: +spread_line.
_SQL = """
CREATE OR REPLACE TABLE team_game AS
WITH sides AS (
    SELECT game_id, season, week, game_type, is_postseason, div_game,
           home_team AS team, away_team AS opponent, TRUE AS home,
           home_score AS points_for, away_score AS points_against,
           home_rest AS rest_days, away_rest AS opp_rest_days,
           -spread_line AS spread, home_qb_id AS starting_qb_id,
           home_qb_name AS starting_qb_name, is_final
    FROM game_ctx
    UNION ALL
    SELECT game_id, season, week, game_type, is_postseason, div_game,
           away_team, home_team, FALSE,
           away_score, home_score,
           away_rest, home_rest,
           spread_line, away_qb_id,
           away_qb_name, is_final
    FROM game_ctx
),
-- score_differential in `pbp` is stated for the team with the ball, so it is
-- negated on the rows where this team is on defence.
play_state AS (
    SELECT game_id, posteam AS team, score_differential AS diff FROM pbp
    WHERE posteam IS NOT NULL AND score_differential IS NOT NULL
    UNION ALL
    SELECT game_id, defteam, -score_differential FROM pbp
    WHERE defteam IS NOT NULL AND score_differential IS NOT NULL
),
game_flow AS (
    SELECT game_id, team,
           max(diff)::INTEGER               AS max_lead,
           min(diff)::INTEGER               AS max_deficit,
           count(*) FILTER (diff > 0)       AS plays_leading,
           count(*) FILTER (diff < 0)       AS plays_trailing,
           count(*) FILTER (diff = 0)       AS plays_tied
    FROM play_state GROUP BY game_id, team
),
offense AS (
    SELECT game_id, posteam AS team,
           count(*) FILTER (play_type IN ('pass','run'))            AS off_plays,
           count(*) FILTER (play_type = 'pass')                     AS pass_attempts,
           count(*) FILTER (play_type = 'run')                      AS rush_attempts,
           sum(epa) FILTER (play_type IN ('pass','run'))            AS off_epa,
           avg(epa) FILTER (play_type IN ('pass','run'))            AS off_epa_per_play,
           avg(success) FILTER (play_type IN ('pass','run'))        AS off_success_rate,
           sum(yards_gained) FILTER (play_type IN ('pass','run'))::INTEGER AS off_yards,
           (sum(interception) + sum(fumble_lost))::INTEGER           AS turnovers,
           sum(sack)::INTEGER                                       AS sacks_taken,
           count(*) FILTER (down = 4 AND play_type NOT IN ('qb_kneel')
                            AND play_type IS NOT NULL)              AS fourth_downs,
           count(*) FILTER (down = 4 AND play_type IN ('pass','run')) AS fourth_down_go
    FROM pbp WHERE posteam IS NOT NULL GROUP BY game_id, posteam
),
defense AS (
    SELECT game_id, defteam AS team,
           count(*) FILTER (play_type IN ('pass','run'))     AS def_plays,
           avg(epa) FILTER (play_type IN ('pass','run'))     AS def_epa_per_play,
           avg(success) FILTER (play_type IN ('pass','run')) AS def_success_rate,
           sum(sack)::INTEGER                                AS sacks,
           (sum(interception) + sum(fumble_lost))::INTEGER   AS takeaways
    FROM pbp WHERE defteam IS NOT NULL GROUP BY game_id, defteam
),
progress AS (
    -- REG games this team has already played this season. Ordered by week, so
    -- byes and the differing season lengths take care of themselves.
    SELECT game_id, team,
           count(*) FILTER (game_type = 'REG')
               OVER (PARTITION BY season, team ORDER BY week
                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS games_played_before
    FROM sides
)
SELECT
    s.game_id, s.season, s.week, s.game_type, s.is_postseason,
    s.team, s.opponent, s.home,
    s.is_final,
    s.points_for, s.points_against,
    s.points_for - s.points_against                       AS margin,
    CASE WHEN s.is_final THEN s.points_for > s.points_against END  AS won,
    CASE WHEN s.is_final THEN s.points_for < s.points_against END  AS lost,
    CASE WHEN s.is_final THEN s.points_for = s.points_against END  AS tied,
    s.rest_days, s.opp_rest_days,
    pr.games_played_before::INTEGER                       AS games_played_before,
    CASE WHEN ss.games_per_team > 0
         THEN pr.games_played_before::DOUBLE / ss.games_per_team END AS season_progress,
    s.spread,
    CASE WHEN s.spread IS NOT NULL THEN s.spread < 0 END  AS favorite,
    CASE WHEN s.spread IS NOT NULL AND s.is_final
         THEN (s.points_for - s.points_against) + s.spread > 0 END AS covered,
    s.div_game,
    s.starting_qb_id, s.starting_qb_name,
    coalesce(o.off_plays, 0)::INTEGER      AS off_plays,
    coalesce(o.pass_attempts, 0)::INTEGER  AS pass_attempts,
    coalesce(o.rush_attempts, 0)::INTEGER  AS rush_attempts,
    CASE WHEN o.off_plays > 0
         THEN o.pass_attempts::DOUBLE / o.off_plays END   AS pass_rate,
    o.off_epa, o.off_epa_per_play, o.off_success_rate, o.off_yards,
    coalesce(o.turnovers, 0)      AS turnovers,
    coalesce(o.sacks_taken, 0)    AS sacks_taken,
    coalesce(o.fourth_downs, 0)::INTEGER   AS fourth_downs,
    coalesce(o.fourth_down_go, 0)::INTEGER AS fourth_down_go,
    CASE WHEN o.fourth_downs > 0
         THEN o.fourth_down_go::DOUBLE / o.fourth_downs END AS fourth_down_go_rate,
    coalesce(d.def_plays, 0)::INTEGER      AS def_plays,
    d.def_epa_per_play, d.def_success_rate,
    coalesce(d.sacks, 0)          AS sacks,
    coalesce(d.takeaways, 0)      AS takeaways,
    f.max_lead, f.max_deficit,
    coalesce(f.plays_leading, 0)::INTEGER  AS plays_leading,
    coalesce(f.plays_trailing, 0)::INTEGER AS plays_trailing,
    coalesce(f.plays_tied, 0)::INTEGER     AS plays_tied
FROM sides s
LEFT JOIN progress   pr ON pr.game_id = s.game_id AND pr.team = s.team
LEFT JOIN season_status ss ON ss.season = s.season
LEFT JOIN offense    o  ON o.game_id = s.game_id  AND o.team = s.team
LEFT JOIN defense    d  ON d.game_id = s.game_id  AND d.team = s.team
LEFT JOIN game_flow  f  ON f.game_id = s.game_id  AND f.team = s.team
"""


def build(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    conn.execute(_SQL)
