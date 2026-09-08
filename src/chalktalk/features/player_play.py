"""`player_play` — who was on the field for each play. Helper, 2016+.

An internal table, not an entity: it exists so `player_game` can say *when* in a
game a player's snaps happened, not just how many there were. That is the whole
basis of D14 — Kirk Cousins tore his Achilles after 61 snaps at 85% share, and
no snap-count heuristic finds him. His last play does.

`unit_play_idx` numbers the plays on which a team's unit was on the field, so
play 1 is that unit's first snap of the game and `team_unit_plays` is its last.
Every player on a play shares its index; a player's own first and last are the
min and max over their rows.

**Scrimmage plays only.** participation fills the offence and defence lists on
kickoffs, punts and kicks too, with the special-teams personnel — and a kickoff
opens the game, so 3,114 of 5,522 "first offensive plays" were kickoffs before
this was filtered. That made a linebacker on the kickoff unit look like an
offensive starter. Restricting to scrimmage plays also keeps this consistent
with `snaps_unit`, which excludes special teams by definition (D12).
"""

from __future__ import annotations

import logging

import duckdb

from chalktalk.config import Settings
from chalktalk.features import team_abbr

log = logging.getLogger(__name__)

_SQL_TEMPLATE = """
CREATE OR REPLACE TABLE player_play AS
WITH plays AS (
    SELECT p.nflverse_game_id AS game_id,
           p.play_id,
           {possession_team} AS off_team,
           p.offense_players,
           p.defense_players
    FROM participation p
    JOIN pbp b ON b.game_id = p.nflverse_game_id AND b.play_id = p.play_id
    WHERE p.play_id IS NOT NULL
      AND p.possession_team IS NOT NULL
      AND b.play_type IS NOT NULL
      AND b.play_type NOT IN ('kickoff', 'punt', 'field_goal', 'extra_point')
),
-- participation names the offence's team but not the defence's; the other team
-- in the game is the defence.
sides AS (
    SELECT p.game_id, p.play_id, 'offense' AS side, p.off_team AS team,
           p.offense_players AS players
    FROM plays p
    WHERE p.offense_players IS NOT NULL AND p.offense_players <> ''
    UNION ALL
    SELECT p.game_id, p.play_id, 'defense',
           CASE WHEN g.home_team = p.off_team THEN g.away_team ELSE g.home_team END,
           p.defense_players
    FROM plays p
    JOIN game_ctx g ON g.game_id = p.game_id
    WHERE p.defense_players IS NOT NULL AND p.defense_players <> ''
),
indexed AS (
    SELECT game_id, play_id, side, team, players,
           row_number() OVER (PARTITION BY game_id, team, side ORDER BY play_id)::INTEGER
               AS unit_play_idx,
           count(*) OVER (PARTITION BY game_id, team, side)::INTEGER AS team_unit_plays
    FROM sides
)
-- DISTINCT because participation occasionally lists a player twice on one play.
SELECT DISTINCT
       game_id,
       play_id,
       unnest(str_split(players, ';')) AS gsis_id,
       side,
       team,
       unit_play_idx,
       team_unit_plays
FROM indexed
"""


def build(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    conn.execute(_SQL_TEMPLATE.format(possession_team=team_abbr.sql("possession_team")))
    rows, games = conn.execute(
        "SELECT count(*), count(DISTINCT game_id) FROM player_play"
    ).fetchone()
    log.info("player_play: %s rows across %s games", f"{rows:,}", games)
