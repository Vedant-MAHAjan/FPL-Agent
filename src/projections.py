"""Build simple, transparent multi-gameweek point projections from bootstrap-static + fixtures.

Preseason caveat (see data/raw findings from step 1): before GW1 is played, bootstrap-static's
season-cumulative fields (total_points, minutes, expected_goals, ...) still hold LAST season's
totals. That's exactly what we want here - it's the only real signal available pre-season - but
it means "new to the PL this summer" players (promotions, transfers in) have no usable history.
Those get a neutral fallback rather than 0, and are flagged as low-confidence.

Horizon, not single-gameweek: squad selection is a multi-week commitment, not a one-week bet, so
projections are summed over a short horizon (default GW1-3) rather than GW1 alone. A single-
gameweek objective was found (step 2 validation against expert previews) to exclude nailed-on
premiums like Haaland purely on one week's points-per-million, which no real manager would do -
see CLAUDE.md build-order notes. Per-player attacking/defensive rates (xG, xA, clean sheets,
defensive contribution, expected minutes) are assumed constant across the horizon - the model has
no game-by-game rotation signal yet, which is exactly the qualitative gap the judgment layer
(component 2) is meant to fill later. Only the fixture-difficulty multiplier varies per gameweek.

Scoring rules are pulled live from bootstrap['game_config']['scoring'] (see load_scoring_rules),
not hardcoded - this caught a real bug during development: goalkeeper goals are worth 10 points,
not 6 (a guessed value that happened to match DEF). Two constants remain hardcoded because they
aren't exposed anywhere in the public API: the defensive-contribution point threshold (10 CBIT
for DEF, 12 for MID/FWD) and the saves-per-point ratio (3 saves = 1 point) - both are stable,
well-documented FPL rules, just not machine-readable ones.
"""

import glob
import json
import os
from datetime import date

POSITION_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

DC_THRESHOLD = {2: 10, 3: 12, 4: 12}  # not exposed via the API; none for GK
SAVES_PER_POINT = 3  # not exposed via the API

UNAVAILABLE_STATUSES = {"i", "s", "u", "n"}  # injured, suspended, unavailable, not available (e.g. on loan)

RELIABLE_MINUTES = 900  # ~10 full games; below this, shrink toward position average
NEW_TO_PL_STARTER_FACTOR = 0.5  # neutral guess for players with zero last-season PL minutes

DEFAULT_START_GW = 1
DEFAULT_HORIZON = 3  # gameweeks

RATE_BASELINE_FIELDS = (
    "minutes",
    "total_points",
    "expected_goals",
    "expected_assists",
    "expected_goals_per_90",
    "expected_assists_per_90",
    "clean_sheets_per_90",
    "defensive_contribution_per_90",
    "goals_conceded_per_90",
    "saves_per_90",
    "bonus",
    "yellow_cards",
    "red_cards",
    "own_goals",
    "penalties_missed",
    "penalties_saved",
)

# Sourced 2026-07-31 (Sky Sports / BBC / football365 reporting on 2026/27 European qualification)
# - not exposed anywhere in the public API, so this needs a manual update if it goes stale. Fixed
# by pre-season qualification; a club being eliminated mid-season doesn't retroactively remove
# the rotation risk from fixture congestion they already carried, so this shouldn't need
# mid-season edits even though it's a snapshot rather than a live-pulled fact.
EUROPEAN_COMPETITIONS_2026_27 = {
    "ARS": "Champions League", "MCI": "Champions League", "MUN": "Champions League",
    "AVL": "Champions League", "LIV": "Champions League",
    "BOU": "Europa League", "SUN": "Europa League", "CRY": "Europa League",
    "BHA": "Conference League",
}

# stat_name -> (bootstrap field to read, True if it's a raw cumulative count needing /minutes*90
# to become a rate, False if bootstrap already exposes it as a *_per_90 field).
RATE_STAT_FIELDS = {
    "xg90": ("expected_goals_per_90", False),
    "xa90": ("expected_assists_per_90", False),
    "cs90": ("clean_sheets_per_90", False),
    "dc90": ("defensive_contribution_per_90", False),
    "conceded90": ("goals_conceded_per_90", False),
    "saves90": ("saves_per_90", False),
    "bonus90": ("bonus", True),
    "yellow90": ("yellow_cards", True),
    "red90": ("red_cards", True),
    "og90": ("own_goals", True),
    "pen_missed90": ("penalties_missed", True),
    "pen_saved90": ("penalties_saved", True),
}


def load_preseason_rate_baseline(raw_dir: str, current_path: str) -> dict | None:
    with open(current_path) as f:
        current = json.load(f)

    first_event = min(
        (event for event in current.get("events", []) if event.get("deadline_time")),
        key=lambda event: event["id"],
        default=None,
    )
    if first_event is None:
        return None
    season_start = date.fromisoformat(first_event["deadline_time"][:10])

    paths = glob.glob(os.path.join(raw_dir, "bootstrap_static_*.json"))
    preseason_paths = [
        path
        for path in paths
        if date.fromisoformat(os.path.basename(path)[len("bootstrap_static_") : -len(".json")])
        < season_start
    ]
    if not preseason_paths:
        return None

    with open(sorted(preseason_paths)[-1]) as f:
        return json.load(f)


def apply_rate_baseline(bootstrap: dict, rate_baseline: dict | None) -> dict:
    if rate_baseline is None:
        return bootstrap

    baseline_by_id = {player["id"]: player for player in rate_baseline["elements"]}
    elements = []
    for player in bootstrap["elements"]:
        baseline_player = baseline_by_id.get(player["id"])
        if baseline_player is None:
            elements.append(player)
            continue
        elements.append(
            {
                **player,
                **{
                    field: baseline_player[field]
                    for field in RATE_BASELINE_FIELDS
                    if field in baseline_player
                },
            }
        )
    return {**bootstrap, "elements": elements}


def _f(player, key):
    val = player.get(key)
    if val is None:
        return 0.0
    return float(val)


def load_scoring_rules(bootstrap: dict) -> dict:
    """Pull the live scoring rulebook instead of hardcoding it - same principle as
    optimizer.squad_rules_from_bootstrap() for squad composition rules."""
    s = bootstrap["game_config"]["scoring"]
    by_pos = lambda d: {pos: d[name] for pos, name in POSITION_NAMES.items()}  # noqa: E731
    return {
        "goals_scored": by_pos(s["goals_scored"]),
        "clean_sheets": by_pos(s["clean_sheets"]),
        "goals_conceded": by_pos(s["goals_conceded"]),
        "defensive_contribution": by_pos(s["defensive_contribution"]),
        "assists": s["assists"],
        "saves": s["saves"],
        "long_play": s["long_play"],
        "short_play": s["short_play"],
        "yellow_cards": s["yellow_cards"],
        "red_cards": s["red_cards"],
        "own_goals": s["own_goals"],
        "penalties_missed": s["penalties_missed"],
        "penalties_saved": s["penalties_saved"],
    }


def load_selectable_players(bootstrap: dict) -> list:
    return [p for p in bootstrap["elements"] if p.get("can_select") and not p.get("removed")]


def _player_rate(player: dict, field: str, is_raw_count: bool, minutes: float) -> float:
    if is_raw_count:
        return (_f(player, field) / minutes * 90) if minutes > 0 else 0.0
    return _f(player, field)


def compute_position_averages(players: list) -> dict:
    """Minutes-weighted average per-90 rates, by position, used as a shrinkage prior."""
    sums = {pos: {"minutes": 0.0, **{k: 0.0 for k in RATE_STAT_FIELDS}} for pos in (1, 2, 3, 4)}
    for p in players:
        minutes = _f(p, "minutes")
        if minutes <= 0:
            continue
        pos = p["element_type"]
        sums[pos]["minutes"] += minutes
        for stat, (field, is_raw) in RATE_STAT_FIELDS.items():
            sums[pos][stat] += _player_rate(p, field, is_raw, minutes) * minutes

    averages = {}
    for pos, s in sums.items():
        m = s["minutes"] or 1.0
        averages[pos] = {stat: s[stat] / m for stat in RATE_STAT_FIELDS}
    return averages


def build_fixtures_by_gw_team(fixtures: list, start_gw: int, num_gws: int) -> dict:
    """gw -> team_id -> list of (opponent_team_id, was_home, difficulty), one entry per fixture.

    An empty list for a team in a given gw means a blank gameweek; more than one entry means a
    double gameweek. Both fall out naturally from just collecting every fixture in range.
    """
    end_gw = start_gw + num_gws - 1
    result = {gw: {} for gw in range(start_gw, end_gw + 1)}
    for f in fixtures:
        gw = f.get("event")
        if gw is None or gw not in result:
            continue
        result[gw].setdefault(f["team_h"], []).append((f["team_a"], True, f["team_h_difficulty"]))
        result[gw].setdefault(f["team_a"], []).append((f["team_h"], False, f["team_a_difficulty"]))
    return result


def _shrunk_rate(player_rate: float, pos_avg_rate: float, minutes: float) -> float:
    shrink = min(1.0, minutes / RELIABLE_MINUTES)
    return shrink * player_rate + (1 - shrink) * pos_avg_rate


def _availability_factor(player: dict) -> float:
    if player["status"] in UNAVAILABLE_STATUSES:
        return 0.0
    cop = player.get("chance_of_playing_next_round")
    if cop is not None:
        return cop / 100.0
    return 1.0


def _starter_factor(player: dict) -> float:
    minutes = _f(player, "minutes")
    if minutes <= 0 and player.get("total_points", 0) == 0:
        return NEW_TO_PL_STARTER_FACTOR  # no PL track record - neutral guess, flagged as low-confidence
    return max(0.1, min(1.0, minutes / 2500))


def _base_player_points(player: dict, pos_avg: dict, scoring: dict) -> dict:
    """Per-fixture point breakdown before any fixture-difficulty adjustment (GW-invariant)."""
    pos = player["element_type"]
    minutes = _f(player, "minutes")
    avg = pos_avg[pos]

    rates = {
        stat: _shrunk_rate(_player_rate(player, field, is_raw, minutes), avg[stat], minutes)
        for stat, (field, is_raw) in RATE_STAT_FIELDS.items()
    }

    availability = _availability_factor(player)
    starter = _starter_factor(player)
    expected_minutes = 90 * availability * starter
    minute_frac = expected_minutes / 90

    attack_pts = (rates["xg90"] * scoring["goals_scored"][pos] + rates["xa90"] * scoring["assists"]) * minute_frac
    cs_pts = rates["cs90"] * scoring["clean_sheets"][pos] * minute_frac
    # goals_conceded is scored per 2 conceded, not per 1
    conceded_pts = (rates["conceded90"] / 2) * scoring["goals_conceded"][pos] * minute_frac
    dc_threshold = DC_THRESHOLD.get(pos)
    dc_pts = (
        scoring["defensive_contribution"][pos] * min(1.0, rates["dc90"] / dc_threshold) * minute_frac
        if dc_threshold else 0.0
    )
    saves_pts = (rates["saves90"] / SAVES_PER_POINT) * scoring["saves"] * minute_frac if pos == 1 else 0.0
    bonus_pts = rates["bonus90"] * minute_frac
    discipline_pts = (
        rates["yellow90"] * scoring["yellow_cards"]
        + rates["red90"] * scoring["red_cards"]
        + rates["og90"] * scoring["own_goals"]
        + rates["pen_missed90"] * scoring["penalties_missed"]
        + rates["pen_saved90"] * scoring["penalties_saved"]
    ) * minute_frac
    # continuous approximation of the real step function (1pt for 1-59 mins, 2pts for 60+)
    appearance_pts = scoring["long_play"] * min(1.0, expected_minutes / 60)

    per_fixture_total = (
        attack_pts + cs_pts + conceded_pts + dc_pts + saves_pts + bonus_pts + discipline_pts + appearance_pts
    )
    low_confidence = minutes <= 0 and player.get("total_points", 0) == 0

    return {
        "expected_minutes": round(expected_minutes, 1),
        "per_fixture_total": per_fixture_total,
        "breakdown": {
            "attack": round(attack_pts, 2),
            "clean_sheet": round(cs_pts, 2),
            "goals_conceded": round(conceded_pts, 2),
            "defensive_contribution": round(dc_pts, 2),
            "saves": round(saves_pts, 2),
            "bonus": round(bonus_pts, 2),
            "discipline": round(discipline_pts, 2),
            "appearance": round(appearance_pts, 2),
        },
        "low_confidence": low_confidence,
    }


def _fdr_mult(fdr: int) -> float:
    return 1 + (3 - fdr) * 0.075 if fdr is not None else 1.0


def _set_piece_role(player: dict) -> dict:
    """Informational only - NOT folded into the points formula. Last season's per-90 goal/assist
    rate already reflects whatever penalties/set pieces that player took last season, so adding a
    separate points bonus here would double-count. The actual value of this data is surfacing
    CHANGES the rate-based model can't see (a player who just became the new #1 penalty taker
    this summer) - that's a judgment-layer read, not something to silently bake into the number.
    order == 1 means first-choice for that set piece; None means not currently in the pecking
    order at all.
    """
    return {
        "penalties": player.get("penalties_order"),
        "corners_indirect_freekicks": player.get("corners_and_indirect_freekicks_order"),
        "direct_freekicks": player.get("direct_freekicks_order"),
    }


def project_player_horizon(
    player: dict, pos_avg: dict, scoring: dict, fixtures_by_gw_team: dict, european_competition_by_team: dict = None
) -> dict:
    pos = player["element_type"]
    team_id = player["team"]
    base = _base_player_points(player, pos_avg, scoring)

    per_gw = []
    total_points = 0.0
    for gw, team_fixtures in fixtures_by_gw_team.items():
        fixtures_this_gw = team_fixtures.get(team_id, [])
        gw_points = 0.0
        gw_fixtures = []
        for opponent, was_home, fdr in fixtures_this_gw:
            mult = _fdr_mult(fdr)
            pts = base["per_fixture_total"] * mult
            gw_points += pts
            gw_fixtures.append({"opponent": opponent, "was_home": was_home, "fdr": fdr, "points": round(pts, 2)})
        per_gw.append({"gw": gw, "fixtures": gw_fixtures, "points": round(gw_points, 2)})
        total_points += gw_points

    return {
        "id": player["id"],
        "web_name": player["web_name"],
        "team": team_id,
        "element_type": pos,
        "now_cost": player["now_cost"],
        "status": player["status"],
        "expected_minutes": base["expected_minutes"],
        "projected_points": round(total_points, 2),
        "per_gw": per_gw,
        "breakdown_per_fixture": base["breakdown"],
        "low_confidence": base["low_confidence"],
        "selected_by_percent": float(player.get("selected_by_percent") or 0),
        "set_pieces": _set_piece_role(player),
        "european_competition": (european_competition_by_team or {}).get(team_id),
    }


def european_competition_by_team(bootstrap: dict) -> dict:
    return {
        t["id"]: EUROPEAN_COMPETITIONS_2026_27[t["short_name"]]
        for t in bootstrap["teams"] if t["short_name"] in EUROPEAN_COMPETITIONS_2026_27
    }


def top_differentials(projections: list, max_ownership: float = 10.0, n: int = 10) -> list:
    """High-projected-points players that are low-owned. The optimizer itself doesn't optimize
    for this - it maximizes raw points, which is the right default for the project's stated
    success metric (cumulative points, not template-beating) - but CLAUDE.md's metric also
    mentions rank, and ownership is free information already sitting in the data, so it's worth
    surfacing as a separate report rather than silently ignoring it.
    """
    candidates = [p for p in projections if p["selected_by_percent"] <= max_ownership and p["status"] == "a"]
    return sorted(candidates, key=lambda p: p["projected_points"], reverse=True)[:n]


def build_multi_gw_projections(
    bootstrap: dict,
    fixtures: list,
    start_gw: int = DEFAULT_START_GW,
    num_gws: int = DEFAULT_HORIZON,
    rate_baseline: dict | None = None,
) -> list:
    projection_bootstrap = apply_rate_baseline(bootstrap, rate_baseline)
    players = load_selectable_players(projection_bootstrap)
    scoring = load_scoring_rules(bootstrap)
    pos_avg = compute_position_averages(players)
    fixtures_by_gw_team = build_fixtures_by_gw_team(fixtures, start_gw, num_gws)
    euro_by_team = european_competition_by_team(bootstrap)
    projections = [
        project_player_horizon(p, pos_avg, scoring, fixtures_by_gw_team, euro_by_team) for p in players
    ]
    return sorted(projections, key=lambda p: p["projected_points"], reverse=True)
