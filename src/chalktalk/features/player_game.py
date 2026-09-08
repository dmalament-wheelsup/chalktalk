"""`player_game` — the `player_game` entity: one row per player per game.

The table the injury question actually runs on. Three groups of columns carry
the weight:

**Participation (`pp_*`, 2016+).** Where in the game a player's snaps happened.
`pp_missed_tail_frac` is the share of his unit's plays that happened after his
last one — the direct expression of "left and did not return" (D14). Kirk
Cousins took 61 snaps at 85% share before tearing his Achilles in the fourth
quarter; no snap-count threshold finds him, and a tail fraction does.

**Baselines.** `baseline_share` is what this player's snap share normally looks
like, from his last four games or, failing that, last season. Without it a
backup's quiet afternoon is indistinguishable from a starter's early exit.

**Linkage.** `team_next_game_id` and `played_team_next_game` are how an inferred
exit gets corroborated, and how a rested starter in the final week is told apart
from an injured one: the rested starter plays the next game (D15).
"""

from __future__ import annotations

import logging

import duckdb

from chalktalk.config import Settings
from chalktalk.features import team_abbr
from chalktalk.features.player_season import PRODUCTION_STATS, _unit_case

log = logging.getLogger(__name__)

#: How many previous games feed `recent_snap_share`.
RECENT_WINDOW = 4

#: How many of the team's following games are checked for a reserve-list move.
RESERVE_LOOKAHEAD = 3


def _recent_lags() -> str:
    """`lag(..., n IGNORE NULLS)` over the last N games in which the player took a snap.

    DuckDB puts IGNORE NULLS inside the offset argument, not after the call.

    Games he did not play are skipped rather than counted as zero, which is what
    "his last four games" means.
    """
    return ",\n           ".join(
        f"lag(qualified_share, {n} IGNORE NULLS) OVER w AS lag_{n}"
        for n in range(1, RECENT_WINDOW + 1)
    )


def _recent_mean() -> str:
    terms = " + ".join(f"coalesce(lag_{n}, 0)" for n in range(1, RECENT_WINDOW + 1))
    count = " + ".join(
        f"CASE WHEN lag_{n} IS NOT NULL THEN 1 ELSE 0 END" for n in range(1, RECENT_WINDOW + 1)
    )
    return f"({terms}) / nullif({count}, 0)"


def _recent_count() -> str:
    return " + ".join(
        f"CASE WHEN lag_{n} IS NOT NULL THEN 1 ELSE 0 END" for n in range(1, RECENT_WINDOW + 1)
    )


def _next_weeks() -> str:
    return ",\n           ".join(
        f"lead(week, {n}) OVER w AS next_week_{n}" for n in range(1, RESERVE_LOOKAHEAD + 1)
    )


def _reserve_weeks() -> str:
    return ", ".join(f"sched.next_week_{n}" for n in range(1, RESERVE_LOOKAHEAD + 1))


def _production_selects() -> str:
    return "\n".join(f"    prod.{stat}," for stat in PRODUCTION_STATS)


def _production_columns() -> str:
    return ", ".join(PRODUCTION_STATS)


_CORE_TEMPLATE = """
CREATE OR REPLACE TEMP TABLE _pg_core AS
WITH snaps AS (
    SELECT DISTINCT ON (s.pfr_player_id, s.game_id)
           s.pfr_player_id, s.game_id, s.season, s.week,
           {team} AS team, s.position AS pfr_position,
           s.offense_snaps, s.defense_snaps, s.st_snaps,
           s.offense_pct, s.defense_pct, s.st_pct
    FROM snap_counts s
    WHERE s.season >= ?
    ORDER BY s.pfr_player_id, s.game_id,
             coalesce(s.offense_snaps, 0) + coalesce(s.defense_snaps, 0)
             + coalesce(s.st_snaps, 0) DESC
),
identified AS (
    SELECT sn.*,
           x.gsis_id,
           x.display_name AS player_name,
           x.position_group,
           coalesce(x.gsis_id, 'pfr:' || sn.pfr_player_id) AS player_key,
           {unit} AS unit
    FROM snaps sn
    LEFT JOIN player_id_xwalk x ON x.pfr_id = sn.pfr_player_id
),
core AS (
    SELECT i.*,
           g.game_type,
           g.is_postseason,
           g.home_team = i.team                                   AS home,
           CASE WHEN g.home_team = i.team THEN g.away_team ELSE g.home_team END AS opponent,
           CASE i.unit WHEN 'offense' THEN i.offense_snaps
                       WHEN 'defense' THEN i.defense_snaps END    AS snaps_unit,
           CASE i.unit WHEN 'offense' THEN i.offense_pct
                       WHEN 'defense' THEN i.defense_pct END      AS snap_share_unit,
           coalesce(i.offense_snaps, 0) + coalesce(i.defense_snaps, 0)
           + coalesce(i.st_snaps, 0)                              AS snaps_total
    FROM identified i
    JOIN game_ctx g ON g.game_id = i.game_id
),
-- The team's own schedule, in order, so byes and the postseason take care of
-- themselves. Bounded to the season: a team's last game has no next game, which
-- is what makes a season-ending exit uncorroborable (D15).
schedule AS (
    SELECT game_id, team, season, week,
           lag(game_id) OVER w  AS team_prev_game_id,
           lead(game_id) OVER w AS team_next_game_id,
           {next_weeks}
    FROM team_game
    WINDOW w AS (PARTITION BY season, team ORDER BY week)
),
recent AS (
    SELECT player_key, game_id,
           {recent_lags}
    FROM (
        SELECT player_key, season, week, game_id,
               CASE WHEN snaps_unit >= 1 THEN snap_share_unit END AS qualified_share
        FROM core
    )
    WINDOW w AS (PARTITION BY player_key, season ORDER BY week)
),
pp_units AS (
    SELECT DISTINCT game_id, team, side, team_unit_plays FROM player_play
),
pp_player AS (
    SELECT game_id, gsis_id, team, side,
           count(*)::INTEGER                        AS pp_plays,
           min(unit_play_idx)::INTEGER              AS pp_first_idx,
           max(unit_play_idx)::INTEGER              AS pp_last_idx,
           max_by(play_id, unit_play_idx)           AS last_play_id
    FROM player_play GROUP BY game_id, gsis_id, team, side
),
injury AS (
    SELECT gsis_id, season, week,
           any_value(report_status)          AS inj_report_status,
           any_value(practice_status)        AS inj_practice_status,
           any_value(coalesce(report_primary_injury, practice_primary_injury))
                                             AS inj_primary_injury,
           bool_or(report_status IS NOT NULL
                   OR practice_status IN ('Did Not Participate In Practice',
                                          'Limited Participation in Practice'))
                                             AS inj_listed
    FROM injuries WHERE gsis_id IS NOT NULL GROUP BY gsis_id, season, week
),
roster AS (
    SELECT DISTINCT ON (gsis_id, season, week) gsis_id, season, week, status
    FROM rosters_weekly WHERE gsis_id IS NOT NULL
    ORDER BY gsis_id, season, week
),
experience AS (
    SELECT gsis_id, season, max(years_exp)::INTEGER AS years_exp
    FROM rosters_weekly WHERE gsis_id IS NOT NULL GROUP BY gsis_id, season
),
production AS (
    SELECT game_id, player_id AS gsis_id, {production_columns}
    FROM player_stats
),
reserve AS (
    SELECT DISTINCT gsis_id, season, week FROM rosters_weekly WHERE status = 'RES'
)
SELECT
    c.player_key, c.gsis_id, c.pfr_player_id AS pfr_id, c.player_name,
    c.season, c.week, c.game_type, c.is_postseason, c.game_id,
    c.team, c.opponent, c.home,
    c.pfr_position AS position, c.position_group, c.unit,
    c.offense_snaps, c.offense_pct, c.defense_snaps, c.defense_pct,
    c.st_snaps, c.st_pct,
    c.snaps_unit, c.snap_share_unit, c.snaps_total,
    roster.status                                        AS roster_status,
    experience.years_exp,
    p.rookie_season = c.season                           AS is_rookie,
    coalesce(tg.starting_qb_id = c.gsis_id, FALSE)       AS is_starting_qb,
    coalesce(injury.inj_listed, FALSE)                   AS inj_listed,
    injury.inj_report_status, injury.inj_practice_status, injury.inj_primary_injury,
{production_selects}
    pp_units.team_unit_plays IS NOT NULL                 AS pp_available,
    pp_player.pp_plays,
    pp_units.team_unit_plays                             AS pp_team_unit_plays,
    pp_player.pp_first_idx,
    pp_player.pp_last_idx,
    (pp_player.pp_first_idx - 1)::DOUBLE
        / nullif(pp_units.team_unit_plays, 0)            AS pp_first_frac,
    pp_player.pp_last_idx::DOUBLE
        / nullif(pp_units.team_unit_plays, 0)            AS pp_last_frac,
    1 - pp_player.pp_last_idx::DOUBLE
        / nullif(pp_units.team_unit_plays, 0)            AS pp_missed_tail_frac,
    (pp_player.pp_first_idx - 1)::DOUBLE
        / nullif(pp_units.team_unit_plays, 0)            AS pp_missed_head_frac,
    b.qtr::INTEGER                                       AS pp_last_qtr,
    b.game_seconds_remaining::INTEGER                    AS pp_last_gsr,
    {recent_mean}                                        AS recent_snap_share,
    ({recent_count})::INTEGER                            AS recent_games,
    ps.snap_share_mean                                   AS prior_season_snap_share,
    ps.games_with_snaps                                  AS prior_season_games,
    coalesce({recent_mean}, ps.snap_share_mean)          AS baseline_share,
    CASE WHEN {recent_mean} IS NOT NULL THEN 'recent'
         WHEN ps.snap_share_mean IS NOT NULL THEN 'prior_season' END AS baseline_source,
    sched.team_prev_game_id,
    sched.team_next_game_id,
    EXISTS (SELECT 1 FROM reserve r
            WHERE r.gsis_id = c.gsis_id AND r.season = c.season
              AND r.week IN ({reserve_weeks}))           AS reserve_within_3_games,
    sched.team_next_game_id IS NOT NULL                  AS corroboration_available
FROM core c
LEFT JOIN recent      ON recent.player_key = c.player_key AND recent.game_id = c.game_id
LEFT JOIN schedule sched ON sched.game_id = c.game_id AND sched.team = c.team
LEFT JOIN pp_units    ON pp_units.game_id = c.game_id AND pp_units.team = c.team
                     AND pp_units.side = c.unit
LEFT JOIN pp_player   ON pp_player.game_id = c.game_id AND pp_player.gsis_id = c.gsis_id
                     AND pp_player.team = c.team AND pp_player.side = c.unit
LEFT JOIN pbp b       ON b.game_id = c.game_id AND b.play_id = pp_player.last_play_id
LEFT JOIN injury      ON injury.gsis_id = c.gsis_id AND injury.season = c.season
                     AND injury.week = c.week
LEFT JOIN roster      ON roster.gsis_id = c.gsis_id AND roster.season = c.season
                     AND roster.week = c.week
LEFT JOIN experience  ON experience.gsis_id = c.gsis_id AND experience.season = c.season
LEFT JOIN players p   ON p.gsis_id = c.gsis_id
LEFT JOIN production prod ON prod.game_id = c.game_id AND prod.gsis_id = c.gsis_id
LEFT JOIN team_game tg ON tg.game_id = c.game_id AND tg.team = c.team
LEFT JOIN player_season ps ON ps.player_key = c.player_key AND ps.season = c.season - 1
"""

# Whether the player played his team's adjacent games can only be answered once
# every row exists, so it is a second pass over the table itself.
_FINAL_SQL = """
CREATE OR REPLACE TABLE player_game AS
SELECT c.*,
       CASE WHEN c.team_prev_game_id IS NOT NULL
            THEN coalesce(prev.snaps_unit >= 1, FALSE) END AS played_team_prev_game,
       CASE WHEN c.team_next_game_id IS NOT NULL
            THEN coalesce(next.snaps_unit >= 1, FALSE) END AS played_team_next_game
FROM _pg_core c
LEFT JOIN _pg_core prev ON prev.player_key = c.player_key
                       AND prev.game_id = c.team_prev_game_id
LEFT JOIN _pg_core next ON next.player_key = c.player_key
                       AND next.game_id = c.team_next_game_id
"""


def build(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    core = _CORE_TEMPLATE.format(
        team=team_abbr.sql("s.team"),
        unit=_unit_case("x.position_group"),
        next_weeks=_next_weeks(),
        recent_lags=_recent_lags(),
        recent_mean=_recent_mean(),
        recent_count=_recent_count(),
        reserve_weeks=_reserve_weeks(),
        production_columns=_production_columns(),
        production_selects=_production_selects(),
    )
    conn.execute(core, [settings.season_floor])
    conn.execute(_FINAL_SQL)
    conn.execute("DROP TABLE _pg_core")
    rows = conn.execute("SELECT count(*) FROM player_game").fetchone()[0]
    log.info("player_game: %s rows", f"{rows:,}")
