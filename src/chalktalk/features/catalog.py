"""The attribute catalog: what every derived column means.

This is the surface `propose_definition` searches and the compiler resolves
against. An attribute with no entry here does not exist as far as the rest of
the system is concerned, which is why the completeness test is a build error and
not a lint.

`play` is the exception: its attributes are the ~370 raw `pbp` columns, so they
are generated from the table itself and only the ones worth a human description
appear in :data:`PLAY_DOCS`.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Literal

import duckdb

from chalktalk.entities import ENTITIES, namespaces

AttrType = Literal["int", "float", "bool", "str", "date"]

#: Used by `propose_definition` to search the surface by theme.
FAMILIES = frozenset(
    {
        "identity",
        "context",
        "result",
        "betting",
        "weather",
        "snaps",
        "roster",
        "injury",
        "participation",
        "baseline",
        "linkage",
        "production",
        "contract",
        "draft",
        "epa",
        "situation",
    }
)

_DUCK_TO_ATTR: dict[str, AttrType] = {
    "BOOLEAN": "bool",
    "TINYINT": "int",
    "SMALLINT": "int",
    "INTEGER": "int",
    "BIGINT": "int",
    "HUGEINT": "int",
    "UTINYINT": "int",
    "USMALLINT": "int",
    "UINTEGER": "int",
    "UBIGINT": "int",
    "FLOAT": "float",
    "DOUBLE": "float",
    "DECIMAL": "float",
    "VARCHAR": "str",
    "DATE": "date",
    "TIMESTAMP": "date",
}


@dataclass(frozen=True)
class Attribute:
    entity: str
    name: str
    type: AttrType
    family: str
    description: str


@dataclass(frozen=True)
class ResolvedAttr:
    namespace: str | None  # None = the entity's own table
    attribute: Attribute


class UnknownAttribute(Exception):
    def __init__(self, ref: str, did_you_mean: list[str]) -> None:
        self.ref = ref
        self.did_you_mean = did_you_mean
        hint = f"; did you mean {', '.join(did_you_mean)}?" if did_you_mean else ""
        super().__init__(f"unknown attribute {ref!r}{hint}")


def _attrs(
    entity: str, rows: list[tuple[str, AttrType, str, str]]
) -> dict[tuple[str, str], Attribute]:
    return {
        (entity, name): Attribute(entity, name, type_, family, description)
        for name, type_, family, description in rows
    }


ATTRIBUTES: dict[tuple[str, str], Attribute] = {}

# Entity tables are documented as each lands (4b: game/team_game/team_season;
# 4c: player_season; 4d: player_game). `player_id_xwalk` and `player_play` are
# internal helpers, not part of the attribute surface.

ATTRIBUTES.update(
    _attrs(
        "game",
        [
            ("game_id", "str", "identity", "nflverse game id, YYYY_WW_AWAY_HOME."),
            ("season", "int", "identity", "Season the game belongs to."),
            (
                "week",
                "int",
                "identity",
                "Week number. Postseason weeks continue the count and moved with the era "
                "(playoffs are 18-21 through 2020, 19-22 from 2021), so test game_type, "
                "never a week number.",
            ),
            ("game_type", "str", "context", "REG, WC, DIV, CON or SB."),
            ("is_postseason", "bool", "context", "game_type is not REG."),
            (
                "reg_weeks",
                "int",
                "context",
                "Number of regular-season weeks in this season: 17 through 2020, 18 from "
                "2021. Read from the schedule, never hard-coded.",
            ),
            (
                "is_final_reg_week",
                "bool",
                "context",
                "A regular-season game in the season's last week - week 17 through 2020, "
                "week 18 from 2021. This is the era-neutral 'rest week', when a team with "
                "its seed settled may sit its starters.",
            ),
            (
                "weeks_remaining_reg",
                "int",
                "context",
                "Regular-season weeks after this one. NULL in the postseason.",
            ),
            ("gameday", "date", "context", "Date the game was played."),
            ("weekday", "str", "context", "Sunday, Monday, Thursday, Friday or Saturday."),
            ("gametime", "str", "context", "Scheduled kickoff, 'HH:MM' Eastern."),
            (
                "kickoff_hour",
                "int",
                "context",
                "Kickoff hour Eastern, 0-23. 20 or later is a primetime window.",
            ),
            ("home_team", "str", "identity", "Home team abbreviation."),
            ("away_team", "str", "identity", "Away team abbreviation."),
            ("home_score", "int", "result", "Home points. NULL until the game is final."),
            ("away_score", "int", "result", "Away points. NULL until the game is final."),
            (
                "result",
                "int",
                "result",
                "home_score - away_score. Positive means the home team won.",
            ),
            ("margin_abs", "int", "result", "Absolute points margin, regardless of who won."),
            ("total", "int", "result", "Combined points scored by both teams."),
            ("overtime", "bool", "result", "The game went to overtime."),
            (
                "is_final",
                "bool",
                "result",
                "The game has been played. Scheduled but unplayed games are in this table too.",
            ),
            (
                "spread_line",
                "float",
                "betting",
                "Closing spread, home-relative: POSITIVE means the home team was favoured by "
                "that many points. This is the negation of the conventional point spread. "
                "Use team_game.spread for a team-relative reading.",
            ),
            ("total_line", "float", "betting", "Closing over/under on combined points."),
            ("div_game", "bool", "context", "The two teams share a division."),
            ("roof", "str", "weather", "dome, outdoors, closed or open."),
            ("surface", "str", "weather", "Playing surface, e.g. grass or fieldturf."),
            ("temp", "int", "weather", "Kickoff temperature in Fahrenheit. NULL indoors."),
            ("wind", "int", "weather", "Wind speed in mph. NULL indoors."),
            ("home_rest", "int", "context", "Days since the home team's previous game."),
            ("away_rest", "int", "context", "Days since the away team's previous game."),
            (
                "home_qb_id",
                "str",
                "identity",
                "gsis_id of the home team's listed starting quarterback.",
            ),
            (
                "home_qb_name",
                "str",
                "identity",
                "Name of the home team's listed starting quarterback.",
            ),
            (
                "away_qb_id",
                "str",
                "identity",
                "gsis_id of the away team's listed starting quarterback.",
            ),
            (
                "away_qb_name",
                "str",
                "identity",
                "Name of the away team's listed starting quarterback.",
            ),
            ("home_coach", "str", "identity", "Home head coach."),
            ("away_coach", "str", "identity", "Away head coach."),
            ("referee", "str", "identity", "Referee."),
            ("stadium", "str", "context", "Stadium name."),
            (
                "plays_total",
                "int",
                "situation",
                "Plays with a real play_type (pass, run, punt, field goal, kickoff, extra "
                "point, kneel, spike). Excludes penalties that wiped out the play.",
            ),
            ("pass_plays", "int", "situation", "Plays with play_type = pass, both teams."),
            ("rush_plays", "int", "situation", "Plays with play_type = run, both teams."),
        ],
    )
)

ATTRIBUTES.update(
    _attrs(
        "team_game",
        [
            ("game_id", "str", "identity", "nflverse game id."),
            ("season", "int", "identity", "Season the game belongs to."),
            (
                "week",
                "int",
                "identity",
                "Week number. Test game_type, not a week number, for the postseason.",
            ),
            ("game_type", "str", "context", "REG, WC, DIV, CON or SB."),
            ("is_postseason", "bool", "context", "game_type is not REG."),
            ("team", "str", "identity", "The team this row is about."),
            ("opponent", "str", "identity", "The other team."),
            ("home", "bool", "context", "This team was at home."),
            ("is_final", "bool", "result", "The game has been played."),
            ("points_for", "int", "result", "Points this team scored."),
            ("points_against", "int", "result", "Points this team allowed."),
            (
                "margin",
                "int",
                "result",
                "points_for - points_against. Negative means this team lost.",
            ),
            ("won", "bool", "result", "This team won. NULL until the game is final."),
            ("lost", "bool", "result", "This team lost. NULL until the game is final."),
            ("tied", "bool", "result", "The game was tied. NULL until the game is final."),
            ("rest_days", "int", "context", "Days since this team's previous game."),
            ("opp_rest_days", "int", "context", "Days since the opponent's previous game."),
            (
                "games_played_before",
                "int",
                "context",
                "Regular-season games this team had already played this season, 0 in week 1. "
                "Counts games, not weeks, so byes are handled. Era-neutral progress.",
            ),
            (
                "season_progress",
                "float",
                "context",
                "games_played_before divided by the season's games per team, 0 to 1. Compare "
                "this rather than week numbers across the 16- and 17-game eras.",
            ),
            (
                "spread",
                "float",
                "betting",
                "Closing spread from this team's point of view: NEGATIVE means this team was "
                "favoured, the conventional reading. NULL when there was no line.",
            ),
            ("favorite", "bool", "betting", "This team was favoured. NULL when there was no line."),
            (
                "covered",
                "bool",
                "betting",
                "margin + spread > 0, i.e. this team beat the closing spread.",
            ),
            ("div_game", "bool", "context", "A divisional matchup."),
            (
                "starting_qb_id",
                "str",
                "identity",
                "gsis_id of this team's listed starting quarterback, from the schedule.",
            ),
            (
                "starting_qb_name",
                "str",
                "identity",
                "Name of this team's listed starting quarterback.",
            ),
            ("off_plays", "int", "situation", "Offensive plays run: passes and designed runs."),
            (
                "pass_attempts",
                "int",
                "situation",
                "Offensive plays with play_type = pass, sacks included.",
            ),
            ("rush_attempts", "int", "situation", "Offensive plays with play_type = run."),
            ("pass_rate", "float", "situation", "pass_attempts / off_plays."),
            ("off_epa", "float", "epa", "Total expected points added on offence."),
            (
                "off_epa_per_play",
                "float",
                "epa",
                "Mean EPA per offensive play. The usual measure of offensive quality.",
            ),
            ("off_success_rate", "float", "epa", "Share of offensive plays with positive EPA."),
            ("off_yards", "int", "production", "Yards gained on passes and runs."),
            ("turnovers", "int", "result", "Interceptions thrown plus fumbles lost."),
            ("sacks_taken", "int", "result", "Times this team's quarterback was sacked."),
            (
                "fourth_downs",
                "int",
                "situation",
                "Fourth-down plays, kneels excluded. Punts and field goals are included, so "
                "this is the denominator for go-for-it rate.",
            ),
            (
                "fourth_down_go",
                "int",
                "situation",
                "Fourth downs where the team ran a play instead of kicking.",
            ),
            (
                "fourth_down_go_rate",
                "float",
                "situation",
                "fourth_down_go / fourth_downs. The usual measure of fourth-down aggression.",
            ),
            ("def_plays", "int", "situation", "Offensive plays faced by this defence."),
            (
                "def_epa_per_play",
                "float",
                "epa",
                "Mean EPA allowed per play. Lower is better, unlike the offensive figure.",
            ),
            (
                "def_success_rate",
                "float",
                "epa",
                "Share of plays faced that had positive EPA for the offence.",
            ),
            ("sacks", "int", "result", "Sacks recorded by this defence."),
            ("takeaways", "int", "result", "Interceptions plus fumble recoveries by this defence."),
            (
                "max_lead",
                "int",
                "result",
                "Largest lead this team held at any snap. Negative if it never led.",
            ),
            (
                "max_deficit",
                "int",
                "result",
                "Largest deficit this team faced at any snap. Positive if it never trailed.",
            ),
            ("plays_leading", "int", "situation", "Snaps played while this team was ahead."),
            ("plays_trailing", "int", "situation", "Snaps played while this team was behind."),
            ("plays_tied", "int", "situation", "Snaps played while the score was level."),
        ],
    )
)

ATTRIBUTES.update(
    _attrs(
        "team_season",
        [
            ("season", "int", "identity", "Season."),
            ("team", "str", "identity", "Team abbreviation."),
            ("games", "int", "context", "Regular-season games played."),
            (
                "games_per_team",
                "int",
                "context",
                "Games each team plays in this season: 16 through 2020, 17 from 2021. Read "
                "from the schedule, never hard-coded.",
            ),
            ("wins", "int", "result", "Regular-season wins."),
            ("losses", "int", "result", "Regular-season losses."),
            ("ties", "int", "result", "Regular-season ties."),
            (
                "win_pct",
                "float",
                "result",
                "(wins + half the ties) / games. Comparable across the 16- and 17-game eras, "
                "unlike a win total.",
            ),
            ("points_for", "int", "result", "Regular-season points scored."),
            ("points_against", "int", "result", "Regular-season points allowed."),
            ("point_diff", "int", "result", "points_for - points_against."),
            ("points_for_per_game", "float", "result", "Points scored per regular-season game."),
            (
                "points_against_per_game",
                "float",
                "result",
                "Points allowed per regular-season game.",
            ),
            (
                "made_playoffs",
                "bool",
                "result",
                "This team played at least one postseason game. Derived from the schedule, "
                "so it is right for every playoff format.",
            ),
            ("playoff_wins", "int", "result", "Postseason wins, 0 to 4."),
            (
                "off_epa_per_play",
                "float",
                "epa",
                "Regular-season mean EPA per offensive play, weighted by plays.",
            ),
            (
                "def_epa_per_play",
                "float",
                "epa",
                "Regular-season mean EPA allowed per play, weighted by plays. Lower is better.",
            ),
            (
                "pass_rate",
                "float",
                "situation",
                "Share of regular-season offensive plays that were passes.",
            ),
            ("division", "str", "identity", "Division, e.g. AFC North."),
            ("conference", "str", "identity", "AFC or NFC."),
        ],
    )
)


ATTRIBUTES.update(
    _attrs(
        "player_season",
        [
            (
                "player_key",
                "str",
                "identity",
                "Stable player key: the gsis_id, or 'pfr:<id>' for the ~0.07% of snap-count "
                "rows that do not crosswalk. Join on this, not on a name.",
            ),
            (
                "gsis_id",
                "str",
                "identity",
                "nflverse player id. NULL if the crosswalk failed.",
            ),
            (
                "pfr_id",
                "str",
                "identity",
                "Pro Football Reference id, which snap counts are keyed by.",
            ),
            (
                "player_name",
                "str",
                "identity",
                "Display name. Not unique; join on player_key.",
            ),
            (
                "season",
                "int",
                "identity",
                "Season.",
            ),
            (
                "position_group",
                "str",
                "identity",
                "QB, RB, WR, TE, OL, DL, LB, DB or SPEC.",
            ),
            (
                "position",
                "str",
                "identity",
                "Most frequent Pro Football Reference position across the season's snap counts. "
                "Finer than position_group: T and G rather than OL.",
            ),
            (
                "unit",
                "str",
                "identity",
                "offense, defense or special, from position_group. Decides which snap column "
                "counts as snaps_unit.",
            ),
            (
                "team_primary",
                "str",
                "identity",
                "Team the player appeared for most often this season.",
            ),
            (
                "teams_count",
                "int",
                "identity",
                "Distinct teams the player recorded snaps for. Above 1 means a mid-season move.",
            ),
            (
                "games",
                "int",
                "snaps",
                "Regular-season games with a snap-count row, whether or not any snaps were played.",
            ),
            (
                "games_with_snaps",
                "int",
                "snaps",
                "Regular-season games with at least one unit snap.",
            ),
            (
                "snaps_unit_total",
                "int",
                "snaps",
                "Total unit snaps: offensive snaps for an offensive player, defensive for a "
                "defensive one, special teams excluded. NULL for special-teams players.",
            ),
            (
                "snaps_total",
                "int",
                "snaps",
                "All snaps including special teams.",
            ),
            (
                "snap_share_mean",
                "float",
                "snaps",
                "Mean share of the team's unit snaps over games in which the player took at "
                "least one. The usual measure of how large a role a player had.",
            ),
            (
                "snap_share_max",
                "float",
                "snaps",
                "Largest single-game unit snap share.",
            ),
            (
                "team_games",
                "int",
                "snaps",
                "Regular-season games the player's primary team played: 16 through 2020, 17 "
                "from 2021.",
            ),
            (
                "games_missed",
                "int",
                "snaps",
                "team_games minus games_with_snaps. Any reason, not only injury.",
            ),
            (
                "games_played_share",
                "float",
                "snaps",
                "games_with_snaps / team_games, 0 to 1. The era-neutral availability measure, "
                "and the default eligibility test for percentiles, which require at least 0.5.",
            ),
            (
                "years_exp",
                "int",
                "roster",
                "Accrued seasons before this one; 0 in a rookie year.",
            ),
            (
                "is_rookie",
                "bool",
                "roster",
                "players.rookie_season equals this season. Disagrees with years_exp "
                "for a few dozen players who accrued time in another league or on "
                "practice squads; the two fields come from different sources.",
            ),
            (
                "draft_year",
                "int",
                "draft",
                "Year drafted. NULL if undrafted.",
            ),
            (
                "draft_round",
                "int",
                "draft",
                "Round drafted, 1 to 7. NULL if undrafted.",
            ),
            (
                "draft_pick",
                "int",
                "draft",
                "Overall pick number. NULL if undrafted.",
            ),
            (
                "undrafted",
                "bool",
                "draft",
                "The player was not drafted.",
            ),
            (
                "contract_year_signed",
                "int",
                "contract",
                "Year the contract in force this season was signed.",
            ),
            (
                "contract_years",
                "int",
                "contract",
                "Length of that contract in years.",
            ),
            (
                "apy",
                "float",
                "contract",
                "Average per year of that contract, in millions of dollars.",
            ),
            (
                "apy_cap_pct",
                "float",
                "contract",
                "Average per year as a share of that season's salary cap, 0 to 1. The "
                "inflation-proof way to compare pay across seasons. Sparse early: about 65% of "
                "player-seasons have a contract in 2013, rising to ~99% from 2017.",
            ),
            (
                "guaranteed",
                "float",
                "contract",
                "Guaranteed money in that contract, in millions.",
            ),
            (
                "attempts",
                "int",
                "production",
                "Pass attempts. Regular season.",
            ),
            (
                "attempts_per_game",
                "float",
                "production",
                "Pass attempts per game played. Totals are not comparable across the 16- and "
                "17-game eras; this is.",
            ),
            (
                "completions",
                "int",
                "production",
                "Completed passes. Regular season.",
            ),
            (
                "completions_per_game",
                "float",
                "production",
                "Completed passes per game played. Totals are not comparable across the 16- and "
                "17-game eras; this is.",
            ),
            (
                "passing_yards",
                "int",
                "production",
                "Passing yards. Regular season.",
            ),
            (
                "passing_yards_per_game",
                "float",
                "production",
                "Passing yards per game played. Totals are not comparable across the 16- and "
                "17-game eras; this is.",
            ),
            (
                "passing_tds",
                "int",
                "production",
                "Passing touchdowns. Regular season.",
            ),
            (
                "passing_tds_per_game",
                "float",
                "production",
                "Passing touchdowns per game played. Totals are not comparable across the 16- "
                "and 17-game eras; this is.",
            ),
            (
                "passing_interceptions",
                "int",
                "production",
                "Interceptions thrown. Regular season.",
            ),
            (
                "passing_interceptions_per_game",
                "float",
                "production",
                "Interceptions thrown per game played. Totals are not comparable across the 16- "
                "and 17-game eras; this is.",
            ),
            (
                "sacks_suffered",
                "int",
                "production",
                "Times sacked. Regular season.",
            ),
            (
                "sacks_suffered_per_game",
                "float",
                "production",
                "Times sacked per game played. Totals are not comparable across the 16- and "
                "17-game eras; this is.",
            ),
            (
                "carries",
                "int",
                "production",
                "Rushing attempts. Regular season.",
            ),
            (
                "carries_per_game",
                "float",
                "production",
                "Rushing attempts per game played. Totals are not comparable across the 16- and "
                "17-game eras; this is.",
            ),
            (
                "rushing_yards",
                "int",
                "production",
                "Rushing yards. Regular season.",
            ),
            (
                "rushing_yards_per_game",
                "float",
                "production",
                "Rushing yards per game played. Totals are not comparable across the 16- and "
                "17-game eras; this is.",
            ),
            (
                "rushing_tds",
                "int",
                "production",
                "Rushing touchdowns. Regular season.",
            ),
            (
                "rushing_tds_per_game",
                "float",
                "production",
                "Rushing touchdowns per game played. Totals are not comparable across the 16- "
                "and 17-game eras; this is.",
            ),
            (
                "targets",
                "int",
                "production",
                "Times targeted as a receiver. Regular season.",
            ),
            (
                "targets_per_game",
                "float",
                "production",
                "Times targeted as a receiver per game played. Totals are not comparable across "
                "the 16- and 17-game eras; this is.",
            ),
            (
                "receptions",
                "int",
                "production",
                "Catches. Regular season.",
            ),
            (
                "receptions_per_game",
                "float",
                "production",
                "Catches per game played. Totals are not comparable across the 16- and 17-game "
                "eras; this is.",
            ),
            (
                "receiving_yards",
                "int",
                "production",
                "Receiving yards. Regular season.",
            ),
            (
                "receiving_yards_per_game",
                "float",
                "production",
                "Receiving yards per game played. Totals are not comparable across the 16- and "
                "17-game eras; this is.",
            ),
            (
                "receiving_tds",
                "int",
                "production",
                "Receiving touchdowns. Regular season.",
            ),
            (
                "receiving_tds_per_game",
                "float",
                "production",
                "Receiving touchdowns per game played. Totals are not comparable across the 16- "
                "and 17-game eras; this is.",
            ),
            (
                "fantasy_points",
                "float",
                "production",
                "Standard-scoring fantasy points. Regular season.",
            ),
            (
                "fantasy_points_per_game",
                "float",
                "production",
                "Standard-scoring fantasy points per game played. Totals are not comparable "
                "across the 16- and 17-game eras; this is.",
            ),
            (
                "fantasy_points_ppr",
                "float",
                "production",
                "PPR-scoring fantasy points. Regular season.",
            ),
            (
                "fantasy_points_ppr_per_game",
                "float",
                "production",
                "PPR-scoring fantasy points per game played. Totals are not comparable across "
                "the 16- and 17-game eras; this is.",
            ),
            (
                "qb_starts",
                "int",
                "roster",
                "Regular-season games in which this player was the team's listed starting "
                "quarterback. 0 for everyone else.",
            ),
            (
                "first_unit_play_games",
                "int",
                "participation",
                "Games in which the player was on the field for his unit's first snap - the "
                "data-derived notion of starting for a non-quarterback. NULL before 2016, when "
                "participation data begins.",
            ),
        ],
    )
)


ATTRIBUTES.update(
    _attrs(
        "player_game",
        [
            (
                "player_key",
                "str",
                "identity",
                "Stable player key: the gsis_id, or 'pfr:<id>' when the crosswalk fails.",
            ),
            (
                "gsis_id",
                "str",
                "identity",
                "nflverse player id. NULL if the crosswalk failed.",
            ),
            (
                "pfr_id",
                "str",
                "identity",
                "Pro Football Reference id, which snap counts are keyed by.",
            ),
            (
                "player_name",
                "str",
                "identity",
                "Display name. Not unique; join on player_key.",
            ),
            (
                "season",
                "int",
                "identity",
                "Season.",
            ),
            (
                "week",
                "int",
                "identity",
                "Week. Postseason weeks continue the count and moved with the era; test "
                "game_type instead.",
            ),
            (
                "game_type",
                "str",
                "context",
                "REG, WC, DIV, CON or SB.",
            ),
            (
                "is_postseason",
                "bool",
                "context",
                "game_type is not REG.",
            ),
            (
                "game_id",
                "str",
                "identity",
                "nflverse game id.",
            ),
            (
                "team",
                "str",
                "identity",
                "The team the player appeared for in this game.",
            ),
            (
                "opponent",
                "str",
                "identity",
                "The other team.",
            ),
            (
                "home",
                "bool",
                "context",
                "The player's team was at home.",
            ),
            (
                "position",
                "str",
                "identity",
                "Pro Football Reference position for this game, finer than position_group.",
            ),
            (
                "position_group",
                "str",
                "identity",
                "QB, RB, WR, TE, OL, DL, LB, DB or SPEC.",
            ),
            (
                "unit",
                "str",
                "identity",
                "offense, defense or special, from position_group.",
            ),
            (
                "offense_snaps",
                "int",
                "snaps",
                "Offensive snaps played, as reported by Pro Football Reference.",
            ),
            (
                "offense_pct",
                "float",
                "snaps",
                "Share of the team's offensive snaps, 0 to 1.",
            ),
            (
                "defense_snaps",
                "int",
                "snaps",
                "Defensive snaps played.",
            ),
            (
                "defense_pct",
                "float",
                "snaps",
                "Share of the team's defensive snaps, 0 to 1.",
            ),
            (
                "st_snaps",
                "int",
                "snaps",
                "Special-teams snaps played.",
            ),
            (
                "st_pct",
                "float",
                "snaps",
                "Share of the team's special-teams snaps, 0 to 1.",
            ),
            (
                "snaps_unit",
                "int",
                "snaps",
                "Snaps on the player's own unit: offensive snaps for an offensive player, "
                "defensive for a defensive one, special teams excluded. NULL for special-teams "
                "players. This is what 'snaps' means unqualified.",
            ),
            (
                "snap_share_unit",
                "float",
                "snaps",
                "snaps_unit as a share of the team's unit snaps, 0 to 1.",
            ),
            (
                "snaps_total",
                "int",
                "snaps",
                "All snaps including special teams.",
            ),
            (
                "roster_status",
                "str",
                "roster",
                "Roster status that week: ACT, RES (reserve/injured), INA, DEV, CUT and others.",
            ),
            (
                "years_exp",
                "int",
                "roster",
                "Accrued seasons before this one; 0 in a rookie year.",
            ),
            (
                "is_rookie",
                "bool",
                "roster",
                "This was the player's first NFL season.",
            ),
            (
                "is_starting_qb",
                "bool",
                "roster",
                "This player was his team's listed starting quarterback for this game. The "
                "listed starter is not always who took the first snap; pp_first_idx = 1 is that "
                "notion.",
            ),
            (
                "inj_listed",
                "bool",
                "injury",
                "The player appeared on this week's injury report with a game status, or did "
                "not practise fully. The report is published before kickoff, so it says nothing "
                "about what happened during the game.",
            ),
            (
                "inj_report_status",
                "str",
                "injury",
                "This week's game designation: Out, Doubtful, Questionable, Probable, or NULL.",
            ),
            (
                "inj_practice_status",
                "str",
                "injury",
                "This week's practice participation: full, limited or did not participate.",
            ),
            (
                "inj_primary_injury",
                "str",
                "injury",
                "The injury named on this week's report, e.g. Knee.",
            ),
            (
                "attempts",
                "int",
                "production",
                "Pass attempts in this game.",
            ),
            (
                "completions",
                "int",
                "production",
                "Completed passes in this game.",
            ),
            (
                "passing_yards",
                "int",
                "production",
                "Passing yards in this game.",
            ),
            (
                "passing_tds",
                "int",
                "production",
                "Passing touchdowns in this game.",
            ),
            (
                "passing_interceptions",
                "int",
                "production",
                "Interceptions thrown in this game.",
            ),
            (
                "sacks_suffered",
                "int",
                "production",
                "Times sacked in this game.",
            ),
            (
                "carries",
                "int",
                "production",
                "Rushing attempts in this game.",
            ),
            (
                "rushing_yards",
                "int",
                "production",
                "Rushing yards in this game.",
            ),
            (
                "rushing_tds",
                "int",
                "production",
                "Rushing touchdowns in this game.",
            ),
            (
                "targets",
                "int",
                "production",
                "Times targeted as a receiver in this game.",
            ),
            (
                "receptions",
                "int",
                "production",
                "Catches in this game.",
            ),
            (
                "receiving_yards",
                "int",
                "production",
                "Receiving yards in this game.",
            ),
            (
                "receiving_tds",
                "int",
                "production",
                "Receiving touchdowns in this game.",
            ),
            (
                "fantasy_points",
                "float",
                "production",
                "Standard-scoring fantasy points in this game.",
            ),
            (
                "fantasy_points_ppr",
                "float",
                "production",
                "PPR-scoring fantasy points in this game.",
            ),
            (
                "pp_available",
                "bool",
                "participation",
                "Play-by-play participation exists for this game and unit, so the pp_* columns "
                "mean something. False before 2016 and for a minority of games after.",
            ),
            (
                "pp_plays",
                "int",
                "participation",
                "Scrimmage plays the player was on the field for, from participation.",
            ),
            (
                "pp_team_unit_plays",
                "int",
                "participation",
                "Scrimmage plays the player's unit was on the field for in this game.",
            ),
            (
                "pp_first_idx",
                "int",
                "participation",
                "Index of the player's first play among his unit's plays, 1 being the unit's "
                "first snap of the game. 1 means he started, derived from the field rather than "
                "a depth chart.",
            ),
            (
                "pp_last_idx",
                "int",
                "participation",
                "Index of the player's last play among his unit's plays.",
            ),
            (
                "pp_first_frac",
                "float",
                "participation",
                "Share of the unit's plays that happened before the player's first, 0 to 1.",
            ),
            (
                "pp_last_frac",
                "float",
                "participation",
                "Share of the unit's plays up to and including the player's last, 0 to 1.",
            ),
            (
                "pp_missed_tail_frac",
                "float",
                "participation",
                "Share of the unit's plays that happened after the player's last one. The "
                "direct measure of leaving and not returning: 0 means he was there at the end, "
                "0.9 means he left almost immediately.",
            ),
            (
                "pp_missed_head_frac",
                "float",
                "participation",
                "Share of the unit's plays before the player entered. High means he came on late.",
            ),
            (
                "pp_last_qtr",
                "int",
                "participation",
                "Quarter of the player's last play. 5 is overtime.",
            ),
            (
                "pp_last_gsr",
                "int",
                "participation",
                "Seconds left in the game at the player's last play.",
            ),
            (
                "recent_snap_share",
                "float",
                "baseline",
                "Mean snap_share_unit over the player's previous four games this season in "
                "which he took a snap. Games he missed are skipped, not counted as zero. NULL "
                "in his first game of a season.",
            ),
            (
                "recent_games",
                "int",
                "baseline",
                "How many games recent_snap_share averages, 0 to 4. A value of 1 makes the "
                "baseline fragile; a definition can require more.",
            ),
            (
                "prior_season_snap_share",
                "float",
                "baseline",
                "The player's snap_share_mean last season. NULL for a rookie or after a missed "
                "year.",
            ),
            (
                "prior_season_games",
                "int",
                "baseline",
                "Games with snaps last season, which says how much the prior-season baseline is "
                "worth.",
            ),
            (
                "baseline_share",
                "float",
                "baseline",
                "What this player's snap share normally looks like: recent_snap_share when "
                "there is one, otherwise prior_season_snap_share. Without it a backup's quiet "
                "afternoon looks like a starter's early exit.",
            ),
            (
                "baseline_source",
                "str",
                "baseline",
                "Which of the two baseline_share came from: recent, prior_season, or NULL if "
                "neither exists.",
            ),
            (
                "team_prev_game_id",
                "str",
                "linkage",
                "The team's previous game this season, by schedule order, so byes are handled. "
                "NULL in week 1.",
            ),
            (
                "team_next_game_id",
                "str",
                "linkage",
                "The team's next game this season, postseason included. NULL after the team's "
                "last game, which is what makes a season-ending exit uncorroborable.",
            ),
            (
                "played_team_prev_game",
                "bool",
                "linkage",
                "The player took a unit snap in the team's previous game. NULL when there was "
                "no previous game.",
            ),
            (
                "played_team_next_game",
                "bool",
                "linkage",
                "The player took a unit snap in the team's next game. NULL when there is no "
                "next game. False after starting is the strongest single sign of an injury "
                "exit, and also what a player who simply lost his job looks like.",
            ),
            (
                "reserve_within_3_games",
                "bool",
                "linkage",
                "The player appears on the reserve list in any of the team's next three game "
                "weeks.",
            ),
            (
                "corroboration_available",
                "bool",
                "linkage",
                "There is a next game, so an inferred exit can be corroborated at all. False "
                "for a team's final game of a season.",
            ),
        ],
    )
)


#: Curated descriptions for the `pbp` columns worth naming. Everything else in
#: `pbp` is still addressable on the `play` entity; it just carries no prose.
PLAY_DOCS: dict[str, str] = {
    "down": "Down, 1-4. NULL on kickoffs, extra points and timeouts.",
    "ydstogo": "Yards needed for a first down.",
    "yardline_100": "Distance to the opponent's end zone, 1-99. 20 or less is the red zone.",
    "qtr": "Quarter, 1-4; 5 is overtime.",
    "game_seconds_remaining": "Seconds left in the game, 3600 at kickoff.",
    "half_seconds_remaining": "Seconds left in the half.",
    "score_differential": "Possession team's score minus the opponent's, before the play.",
    "play_type": "pass, run, punt, field_goal, kickoff, extra_point, qb_kneel, qb_spike, no_play.",
    "pass": "1 if the play was a pass attempt, sack or scramble.",
    "rush": "1 if the play was a designed run.",
    "epa": "Expected points added by this play for the possession team.",
    "wpa": "Win probability added by this play for the possession team.",
    "wp": "Possession team's win probability before the play, 0-1.",
    "success": "1 if EPA was positive.",
    "air_yards": "Yards the ball travelled past the line of scrimmage before the catch point.",
    "yards_after_catch": "Yards gained after the reception.",
    "yards_gained": "Net yards gained on the play.",
    "touchdown": "1 if the play ended in a touchdown by either team.",
    "interception": "1 if the pass was intercepted.",
    "fumble_lost": "1 if a fumble was lost to the defence.",
    "sack": "1 if the quarterback was sacked.",
    "penalty": "1 if a penalty was called on the play.",
    "fourth_down_converted": "1 if a fourth-down attempt gained the first down or scored.",
    "fourth_down_failed": "1 if a fourth-down attempt fell short.",
    "third_down_converted": "1 if a third-down attempt gained the first down or scored.",
    "field_goal_result": "made, missed or blocked.",
    "two_point_attempt": "1 if the play was a two-point conversion attempt.",
    "posteam": "Team with the ball.",
    "defteam": "Team on defence.",
    "passer_player_id": "gsis_id of the passer.",
    "rusher_player_id": "gsis_id of the ball carrier on a designed run.",
    "receiver_player_id": "gsis_id of the targeted receiver.",
    "shotgun": "1 if the offence lined up in shotgun.",
    "no_huddle": "1 if the offence ran the play without huddling.",
    "qb_dropback": "1 if the quarterback dropped back to pass, including sacks and scrambles.",
    "qb_scramble": "1 if a called pass became a quarterback run.",
    "pass_length": "short or deep.",
    "pass_location": "left, middle or right.",
    "run_location": "left, middle or right.",
    "run_gap": "end, tackle or guard.",
    "desc": "The play-by-play text description.",
}


def _play_attributes(conn: duckdb.DuckDBPyConnection) -> list[Attribute]:
    """The `play` entity is the raw `pbp` table; describe what we can, expose it all."""
    rows = conn.execute(f'DESCRIBE "{ENTITIES["play"].table}"').fetchall()
    return [
        Attribute(
            "play",
            name,
            _DUCK_TO_ATTR.get(duck_type.split("(")[0], "str"),
            "situation",
            PLAY_DOCS.get(name, ""),
        )
        for name, duck_type, *_ in rows
    ]


def attributes_for(entity: str, conn: duckdb.DuckDBPyConnection) -> list[Attribute]:
    """Every attribute addressable on ``entity``'s own table."""
    if entity not in ENTITIES:
        raise UnknownAttribute(entity, sorted(ENTITIES))
    if entity == "play":
        return _play_attributes(conn)
    return sorted(
        (a for (e, _), a in ATTRIBUTES.items() if e == entity), key=lambda a: (a.family, a.name)
    )


def _entity_of_namespace(entity: str, namespace: str) -> str:
    """Which entity a namespace's rows belong to, so its attributes can be looked up."""
    join = namespaces(entity)[namespace]
    for name, spec in ENTITIES.items():
        if spec.table == join.table:
            return name
    raise KeyError(f"namespace {namespace!r} of {entity} points at unknown table {join.table}")


def resolve(entity: str, ref: str, conn: duckdb.DuckDBPyConnection) -> ResolvedAttr:
    """``"name"`` or ``"ns.name"`` → the attribute it names.

    Raises :class:`UnknownAttribute` with suggestions rather than guessing.
    """
    if entity not in ENTITIES:
        raise UnknownAttribute(ref, sorted(ENTITIES))

    namespace: str | None = None
    name = ref
    if "." in ref:
        namespace, _, name = ref.partition(".")
        available = namespaces(entity)
        if namespace not in available:
            raise UnknownAttribute(
                ref, [f"{n}.{name}" for n in difflib.get_close_matches(namespace, available, n=3)]
            )
        target_entity = _entity_of_namespace(entity, namespace)
    else:
        target_entity = entity

    for attribute in attributes_for(target_entity, conn):
        if attribute.name == name:
            return ResolvedAttr(namespace, attribute)

    known = [a.name for a in attributes_for(target_entity, conn)]
    prefix = f"{namespace}." if namespace else ""
    raise UnknownAttribute(
        ref, [prefix + m for m in difflib.get_close_matches(name, known, n=3, cutoff=0.6)]
    )


def documented_entities() -> list[str]:
    """Entities whose attributes are hand-written here (everything but `play`)."""
    return [e for e in ENTITIES if e != "play"]


def _existing_columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[str] | None:
    found = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
        [table],
    ).fetchone()
    if not found:
        return None
    return [row[0] for row in conn.execute(f'DESCRIBE "{table}"').fetchall()]


def undocumented(conn: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """Columns of built entity tables with no ATTRIBUTES entry.

    A non-empty result is the feature layer being incomplete, not a style
    problem: an undocumented column is invisible to `propose_definition` and
    unresolvable by the compiler.
    """
    missing: dict[str, list[str]] = {}
    for entity in documented_entities():
        columns = _existing_columns(conn, ENTITIES[entity].table)
        if columns is None:
            continue  # not built yet
        absent = [c for c in columns if (entity, c) not in ATTRIBUTES]
        if absent:
            missing[entity] = absent
    return missing


def orphaned(conn: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """ATTRIBUTES entries naming a column the built table does not have (typos)."""
    orphans: dict[str, list[str]] = {}
    for entity in documented_entities():
        columns = _existing_columns(conn, ENTITIES[entity].table)
        if columns is None:
            continue
        extra = [name for (e, name) in ATTRIBUTES if e == entity and name not in columns]
        if extra:
            orphans[entity] = sorted(extra)
    return orphans
