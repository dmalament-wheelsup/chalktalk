"""A tiny hand-made league, run through the production builders.

The point is that nothing here re-implements the feature layer: the raw tables
are invented, and then `features.build_all` and `coverage.build` — the same code
a real build runs — turn them into the entity tables the tests query. Schema
drift between production SQL and test fixtures is therefore impossible.

Two seasons, two teams, three regular-season weeks and a postseason game, and
eight players chosen so the interesting cases exist in miniature: a full-time
starter, an early exit, a rested starter in the final regular-season week, and a
backup cameo.
"""

from __future__ import annotations

import duckdb
import polars as pl

from chalktalk import coverage as coverage_mod
from chalktalk import features
from chalktalk.config import Settings

SEASONS = (2022, 2023)
TEAMS = ("AAA", "BBB")
REG_WEEKS = 3
POST_WEEK = 4
#: Scrimmage plays each team's unit runs in a game. Chosen so a player who takes
#: 3 of them is unmistakably an exit.
UNIT_PLAYS = 60

#: (gsis_id, name, pfr_id, position, position_group, rookie_season, draft)
PLAYERS = [
    ("00-0000001", "Starter Quarterback", "StaQ01", "QB", "QB", 2018, (2018, 1, 3)),
    ("00-0000002", "Backup Quarterback", "BacQ01", "QB", "QB", 2020, (2020, 6, 190)),
    ("00-0000003", "Exit Runningback", "ExiR01", "RB", "RB", 2019, (2019, 2, 40)),
    ("00-0000004", "Rested Tackle", "ResT01", "T", "OL", 2016, (2016, 1, 12)),
    ("00-0000005", "Every Down Linebacker", "EveL01", "LB", "LB", 2017, (2017, 3, 70)),
    ("00-0000006", "Rotational Lineman", "RotD01", "DE", "DL", 2021, None),
    ("00-0000007", "Rookie Receiver", "RooW01", "WR", "WR", 2023, (2023, 4, 120)),
    ("00-0000008", "Kicker Guy", "KicK01", "K", "SPEC", 2015, None),
]

OFFENSE = ["00-0000001", "00-0000003", "00-0000004", "00-0000007", "00-0000002"]
DEFENSE = ["00-0000005", "00-0000006"]

#: The one early exit: 3 of 60 unit plays in 2023 week 2, then the reserve list.
EXIT = ("00-0000003", 2023, 2)
#: Rested in the last regular-season week, and plays the postseason game.
RESTED = ("00-0000004", 2023, REG_WEEKS)


def _games() -> list[dict]:
    games = []
    for season in SEASONS:
        for week in range(1, REG_WEEKS + 1):
            home, away = (TEAMS[0], TEAMS[1]) if week % 2 else (TEAMS[1], TEAMS[0])
            games.append(
                {
                    "game_id": f"{season}_{week:02d}_{away}_{home}",
                    "season": season,
                    "week": week,
                    "game_type": "REG",
                    "home_team": home,
                    "away_team": away,
                    "home_score": 20 + week,
                    "away_score": 17,
                }
            )
        games.append(
            {
                "game_id": f"{season}_{POST_WEEK:02d}_{TEAMS[1]}_{TEAMS[0]}",
                "season": season,
                "week": POST_WEEK,
                "game_type": "WC",
                "home_team": TEAMS[0],
                "away_team": TEAMS[1],
                "home_score": 24,
                "away_score": 21,
            }
        )
    return games


def _snap_rows(games: list[dict]) -> list[dict]:
    """Snap counts per player per game, with the four interesting shapes."""
    rows = []
    for game in games:
        for team in TEAMS:
            for gsis, name, pfr, position, group, _rookie, _draft in PLAYERS:
                # The rookie receiver only exists from his rookie season.
                if gsis == "00-0000007" and game["season"] < 2023:
                    continue
                on_offense = gsis in OFFENSE
                unit_snaps = UNIT_PLAYS
                if gsis == "00-0000002":  # backup quarterback: cameos only
                    unit_snaps = 2
                elif gsis == "00-0000006":  # rotational lineman
                    unit_snaps = 30
                elif gsis == "00-0000007":  # rookie, growing role
                    unit_snaps = 20 if game["week"] < 3 else 45
                if (gsis, game["season"], game["week"]) == EXIT and team == TEAMS[0]:
                    unit_snaps = 3
                if (gsis, game["season"], game["week"]) == RESTED and team == TEAMS[0]:
                    unit_snaps = 12
                # Only the first team fields these players; the second team has
                # its own anonymous roster, which nothing tests.
                if team != TEAMS[0]:
                    continue
                rows.append(
                    {
                        "game_id": game["game_id"],
                        "pfr_game_id": game["game_id"].lower(),
                        "season": game["season"],
                        "game_type": game["game_type"],
                        "week": game["week"],
                        "player": name,
                        "pfr_player_id": pfr,
                        "position": position,
                        "team": team,
                        "opponent": TEAMS[1],
                        "offense_snaps": unit_snaps if on_offense else 0,
                        "offense_pct": (unit_snaps / UNIT_PLAYS) if on_offense else 0.0,
                        "defense_snaps": 0 if on_offense else unit_snaps,
                        "defense_pct": 0.0 if on_offense else unit_snaps / UNIT_PLAYS,
                        "st_snaps": 4 if group == "SPEC" else 0,
                        "st_pct": 0.2 if group == "SPEC" else 0.0,
                    }
                )
    return rows


def _participation(games: list[dict]) -> list[dict]:
    """One row per scrimmage play, 2023 only, so pp_* has a covered and an uncovered era."""
    rows = []
    for game in games:
        if game["season"] != 2023:
            continue
        for idx in range(1, UNIT_PLAYS + 1):
            offense = list(OFFENSE)
            # The exit: on the field for the first three plays only.
            if (game["season"], game["week"]) == (EXIT[1], EXIT[2]) and idx > 3:
                offense = [p for p in offense if p != EXIT[0]]
            # The rested tackle leaves after a fifth of the game.
            if (game["season"], game["week"]) == (RESTED[1], RESTED[2]) and idx > 12:
                offense = [p for p in offense if p != RESTED[0]]
            # The backup quarterback appears only at the very end.
            if idx <= UNIT_PLAYS - 2:
                offense = [p for p in offense if p != "00-0000002"]
            rows.append(
                {
                    "nflverse_game_id": game["game_id"],
                    "play_id": float(idx),
                    "possession_team": TEAMS[0],
                    "offense_players": ";".join(offense),
                    "defense_players": ";".join(DEFENSE),
                    "n_offense": len(offense),
                    "n_defense": len(DEFENSE),
                }
            )
    return rows


def _pbp(games: list[dict]) -> list[dict]:
    rows = []
    for game in games:
        for idx in range(1, UNIT_PLAYS + 1):
            rows.append(
                {
                    "game_id": game["game_id"],
                    "play_id": float(idx),
                    "season": game["season"],
                    "week": game["week"],
                    "posteam": TEAMS[0],
                    "defteam": TEAMS[1],
                    "play_type": "pass" if idx % 2 else "run",
                    "down": float(idx % 4 + 1),
                    "ydstogo": 10.0,
                    "yardline_100": 50.0,
                    "qtr": float(min(4, idx // 15 + 1)),
                    "game_seconds_remaining": float(3600 - idx * 55),
                    "epa": 0.1 if idx % 2 else -0.1,
                    "success": 1.0 if idx % 2 else 0.0,
                    "yards_gained": 6.0 if idx % 2 else 3.0,
                    "interception": 0.0,
                    "fumble_lost": 0.0,
                    "sack": 0.0,
                    "score_differential": 3.0,
                    "wp": 0.55,
                    "touchdown": 0.0,
                    "penalty": 0.0,
                    "desc": f"({idx}) play {idx} of {game['game_id']}",
                }
            )
    return rows


def _weekly_rows(games: list[dict]) -> tuple[list[dict], list[dict]]:
    """rosters_weekly and injuries, including the exit's reserve move and report."""
    rosters, injuries = [], []
    for game in games:
        for gsis, name, pfr, position, _group, _rookie, _draft in PLAYERS:
            if gsis == "00-0000007" and game["season"] < 2023:
                continue
            reserved = gsis == EXIT[0] and game["season"] == EXIT[1] and game["week"] > EXIT[2]
            rosters.append(
                {
                    "season": game["season"],
                    "week": game["week"],
                    "team": TEAMS[0],
                    "gsis_id": gsis,
                    "pfr_id": pfr,
                    "full_name": name,
                    "position": position,
                    "status": "RES" if reserved else "ACT",
                    "years_exp": game["season"] - 2015,
                    "game_type": game["game_type"],
                }
            )
            if reserved:
                injuries.append(
                    {
                        "season": game["season"],
                        "week": game["week"],
                        "game_type": game["game_type"],
                        "team": TEAMS[0],
                        "gsis_id": gsis,
                        "full_name": name,
                        "position": position,
                        "report_status": "Out",
                        "practice_status": "Did Not Participate In Practice",
                        "report_primary_injury": "Knee",
                        "practice_primary_injury": "Knee",
                    }
                )
    return rosters, injuries


def _player_stats(games: list[dict]) -> list[dict]:
    rows = []
    for game in games:
        for gsis, name, _pfr, position, group, _rookie, _draft in PLAYERS:
            if gsis == "00-0000007" and game["season"] < 2023:
                continue
            rows.append(
                {
                    "player_id": gsis,
                    "player_name": name,
                    "position": position,
                    "position_group": group,
                    "season": game["season"],
                    "week": game["week"],
                    "season_type": "REG" if game["game_type"] == "REG" else "POST",
                    "game_id": game["game_id"],
                    "team": TEAMS[0],
                    "opponent_team": TEAMS[1],
                    "attempts": 30 if group == "QB" else 0,
                    "completions": 20 if group == "QB" else 0,
                    "passing_yards": 250 if group == "QB" else 0,
                    "passing_tds": 2 if group == "QB" else 0,
                    "passing_interceptions": 0,
                    "sacks_suffered": 1 if group == "QB" else 0,
                    "carries": 18 if group == "RB" else 0,
                    "rushing_yards": 80 if group == "RB" else 0,
                    "rushing_tds": 1 if group == "RB" else 0,
                    "targets": 8 if group == "WR" else 0,
                    "receptions": 6 if group == "WR" else 0,
                    "receiving_yards": 70 if group == "WR" else 0,
                    "receiving_tds": 1 if group == "WR" else 0,
                    "fantasy_points": 12.0,
                    "fantasy_points_ppr": 15.0,
                }
            )
    return rows


def build_mini(settings: Settings) -> duckdb.DuckDBPyConnection:
    """An in-memory database with the same shape a real build produces."""
    conn = duckdb.connect()
    games = _games()
    rosters, injuries = _weekly_rows(games)

    raw = {
        "schedules": [
            {
                **g,
                "gameday": f"{g['season']}-09-{10 + g['week']:02d}",
                "weekday": "Sunday",
                "gametime": "13:00",
                "result": g["home_score"] - g["away_score"],
                "total": g["home_score"] + g["away_score"],
                "overtime": 0,
                "spread_line": 3.0,
                "total_line": 44.0,
                "div_game": 1,
                "roof": "outdoors",
                "surface": "grass",
                "temp": None,
                "wind": None,
                "home_rest": 7,
                "away_rest": 7,
                "home_qb_id": "00-0000001",
                "away_qb_id": "00-0000001",
                "home_qb_name": "Starter Quarterback",
                "away_qb_name": "Starter Quarterback",
                "home_coach": "Coach A",
                "away_coach": "Coach B",
                "referee": "Ref",
                "stadium": "Mini Field",
            }
            for g in games
        ],
        "snap_counts": _snap_rows(games),
        "participation": _participation(games),
        "pbp": _pbp(games),
        "rosters_weekly": rosters,
        "injuries": injuries,
        "player_stats": _player_stats(games),
        "players": [
            {
                "gsis_id": gsis,
                "pfr_id": pfr,
                "display_name": name,
                "position": position,
                "position_group": group,
                "rookie_season": rookie,
                "draft_year": draft[0] if draft else None,
                "draft_round": draft[1] if draft else None,
                "draft_pick": draft[2] if draft else None,
                "years_of_experience": 5,
            }
            for gsis, name, pfr, position, group, rookie, draft in PLAYERS
        ],
        "contracts": [
            {
                "player": name,
                "gsis_id": gsis,
                "position": position,
                "team": TEAMS[0],
                "year_signed": 2021,
                "years": 5,
                "value": 100.0,
                "apy": 20.0,
                "guaranteed": 60.0,
                "apy_cap_pct": 0.10 if group == "QB" else 0.04,
            }
            for gsis, name, _pfr, position, group, _rookie, _draft in PLAYERS
        ],
        "draft_picks": [
            {
                "season": draft[0],
                "round": draft[1],
                "pick": draft[2],
                "gsis_id": gsis,
                "pfr_player_name": name,
            }
            for gsis, name, _pfr, _position, _group, _rookie, draft in PLAYERS
            if draft
        ],
        "teams": [
            {
                "team_abbr": team,
                "team_name": f"{team} Team",
                "team_conf": "AFC",
                "team_division": "AFC North",
            }
            for team in TEAMS
        ],
    }

    for table, rows in raw.items():
        frame = pl.DataFrame(rows, infer_schema_length=None)
        conn.register("_mini", frame.to_arrow())
        conn.execute(f'CREATE TABLE "{table}" AS SELECT * FROM _mini')
        conn.unregister("_mini")

    conn.execute("ALTER TABLE schedules ALTER season TYPE INTEGER")
    conn.execute("ALTER TABLE schedules ALTER week TYPE INTEGER")

    coverage_mod.build_season_status(conn, settings)
    features.build_all(conn, settings)
    coverage_mod.build(conn, settings)
    return conn
