"""Football words, and what they might be grounded in.

This is not a dictionary of meanings — the whole premise is that these words do
not have single meanings. It is a map from a word a person might type to the
attributes and definitions that could plausibly stand for it, so that
`propose_definition` can offer candidates the user chooses between.

`not_computable` is the honest half. "Pro Bowler" has no answer in this data, and
saying so is worth more than a plausible substitute.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Family:
    id: str
    words: tuple[str, ...]
    suggest_definitions: tuple[str, ...] = ()
    suggest_attrs: tuple[str, ...] = ()
    not_computable: str | None = None
    note: str = ""


VOCABULARY: list[Family] = [
    Family(
        "star",
        ("star", "elite", "stud", "top", "best", "great", "premier"),
        suggest_definitions=("star_by_snaps", "star_by_contract", "star_by_draft"),
        suggest_attrs=("snap_share_mean", "apy_cap_pct", "draft_round", "games_played_share"),
        note="No 'star' marker exists. Snap share, pay and draft capital each say "
        "something different, and they disagree about real players.",
    ),
    Family(
        "starter",
        ("starter", "starting", "first string", "started", "first team"),
        suggest_attrs=("is_starting_qb", "pp_first_idx", "snap_share_unit", "baseline_share"),
        note="For quarterbacks the schedule names a starter. For everyone else, "
        "pp_first_idx = 1 means on the field for the unit's first snap (2016+).",
    ),
    Family(
        "backup",
        ("backup", "reserve", "second string", "bench", "understudy"),
        suggest_attrs=("snap_share_unit", "baseline_share", "is_starting_qb", "pp_first_idx"),
    ),
    Family(
        "rookie",
        ("rookie", "first year", "debut", "freshman"),
        suggest_attrs=("is_rookie", "years_exp", "draft_year"),
    ),
    Family(
        "veteran",
        ("veteran", "vet", "experienced", "long in the tooth"),
        suggest_attrs=("years_exp",),
    ),
    Family(
        "injury",
        ("injury", "injured", "hurt", "exit", "left the game", "went down", "knocked out"),
        suggest_definitions=("early_exit", "left_early", "snap_drop"),
        suggest_attrs=(
            "pp_missed_tail_frac",
            "inj_listed",
            "inj_report_status",
            "reserve_within_3_games",
            "played_team_next_game",
            "roster_status",
        ),
        note="Nothing records in-game exits. The injuries table is the weekly "
        "report, published before kickoff, so an exit has to be inferred from "
        "when a player's snaps stopped and corroborated by what happened next.",
    ),
    Family(
        "rest",
        ("rest", "rested", "sat", "benched", "sat out", "load management"),
        suggest_attrs=("is_final_reg_week", "played_team_next_game", "snap_share_unit"),
        note="A rested starter in the last week looks identical to an injury "
        "exit until you check whether he played the following game.",
    ),
    Family(
        "workhorse",
        ("workhorse", "bellcow", "bell cow", "feature back", "every down"),
        suggest_attrs=("carries", "carries_per_game", "snap_share_mean", "snaps_unit_total"),
    ),
    Family(
        "primetime",
        ("primetime", "prime time", "night game", "snf", "mnf", "tnf", "sunday night"),
        suggest_attrs=("kickoff_hour", "weekday"),
    ),
    Family(
        "blowout",
        ("blowout", "rout", "laugher", "one sided", "thrashing"),
        suggest_attrs=("margin_abs", "margin", "max_lead", "max_deficit"),
    ),
    Family(
        "close game",
        ("close", "one score", "nail biter", "tight", "down to the wire"),
        suggest_attrs=("margin_abs", "margin", "plays_tied"),
    ),
    Family(
        "favorite",
        ("favorite", "favourite", "underdog", "dog", "spread", "line"),
        suggest_attrs=("spread", "favorite", "covered", "spread_line"),
        note="team_game.spread is team-relative and negative means favoured — "
        "the opposite sign to nflverse's home-relative spread_line.",
    ),
    Family(
        "playoff team",
        ("playoff", "playoffs", "postseason", "made the playoffs"),
        suggest_attrs=("made_playoffs", "is_postseason", "playoff_wins", "game_type"),
    ),
    Family(
        "division",
        ("division", "divisional", "rival", "in division"),
        suggest_attrs=("div_game", "division", "conference"),
    ),
    Family(
        "home road",
        ("home", "road", "away", "travelling", "traveling"),
        suggest_attrs=("home", "home_team", "away_team"),
    ),
    Family(
        "short week",
        ("short week", "rest days", "thursday game", "extra rest", "bye"),
        suggest_attrs=("rest_days", "opp_rest_days", "home_rest", "away_rest"),
    ),
    Family(
        "weather",
        ("weather", "cold", "wind", "windy", "dome", "indoors", "snow", "rain"),
        suggest_attrs=("temp", "wind", "roof", "surface"),
    ),
    Family(
        "garbage time",
        ("garbage time", "mop up", "scrub time"),
        suggest_attrs=("wp", "score_differential", "game_seconds_remaining"),
        note="A play-level idea: win probability far from 0.5 late in a game.",
    ),
    Family(
        "red zone",
        ("red zone", "redzone", "inside the twenty", "goal to go"),
        suggest_attrs=("yardline_100", "goal_to_go"),
    ),
    Family(
        "two minute",
        ("two minute", "two-minute", "hurry up", "final drive", "end of half"),
        suggest_attrs=("half_seconds_remaining", "game_seconds_remaining", "no_huddle"),
    ),
    Family(
        "fourth down",
        ("fourth down", "4th down", "going for it", "aggression", "aggressive"),
        suggest_attrs=(
            "fourth_down_go_rate",
            "fourth_downs",
            "fourth_down_go",
            "fourth_down_converted",
            "down",
        ),
    ),
    Family(
        "deep ball",
        ("deep ball", "deep shot", "downfield", "bomb", "air yards"),
        suggest_attrs=("air_yards", "pass_length", "passing_air_yards"),
    ),
    Family(
        "pro bowl",
        ("pro bowl", "pro bowler", "all pro", "all-pro", "honours", "honors", "accolades"),
        not_computable="nflverse has no per-season honours table. draft_picks.probowls "
        "is a career total, so it cannot say who was selected in a given year. "
        "Snap share, pay or production can stand in, but they are different questions.",
    ),
    Family(
        "pressure",
        ("pressure", "pressured", "hurried", "hit", "pass rush"),
        not_computable="participation carries was_pressure from 2018, but it is not "
        "exposed as an attribute in v1. Sacks and QB hits are available on the play "
        "entity in the meantime.",
    ),
]

_BY_WORD: dict[str, list[Family]] = {}
for _family in VOCABULARY:
    for _word in _family.words:
        _BY_WORD.setdefault(_word, []).append(_family)


def families_for(text: str) -> list[Family]:
    """Every family whose words appear in ``text``, longest phrase first."""
    lowered = f" {text.lower().strip()} "
    hits: list[tuple[int, Family]] = []
    for family in VOCABULARY:
        best = max(
            (len(w) for w in family.words if f" {w} " in lowered or w in lowered.split()),
            default=0,
        )
        if best:
            hits.append((best, family))
    return [family for _, family in sorted(hits, key=lambda h: -h[0])]
