"""Season-level chip planner (component 4): long-horizon reasoning about wildcard / bench-boost
/ triple-captain / free-hit timing, run every 3-4 gameweeks (not weekly) per CLAUDE.md.

Like the judgment layer (step 3), the actual timing *decision* is manual/hand-reasoned for now -
this module builds the real inputs a human (or an LLM acting as one) needs to make that call:
fixture-swing scores per team, blank/double gameweek detection, and the caller's remaining chip
inventory (from memory.get_squad_state()). No blank/double gameweeks exist this early in a
season - they get announced mid-season via cup-round scheduling, per CLAUDE.md - so that signal
is expected to be empty for GW1-era planning, not a bug.
"""

from projections import build_fixtures_by_gw_team

CHIP_PLANNING_HORIZON = 6  # gameweeks to look ahead when scoring fixture swings


def team_fixture_swing(fixtures: list, teams: list, start_gw: int, num_gws: int) -> dict:
    """team_id -> {avg_fdr, num_fixtures, gws_with_blank, gws_with_double} over the horizon.
    Lower avg_fdr = easier run (favors wildcard-in / bench-boost timing for that team's assets).
    """
    fixtures_by_gw_team = build_fixtures_by_gw_team(fixtures, start_gw, num_gws)
    team_ids = [t["id"] for t in teams]

    swing = {t: {"fdrs": [], "gws_with_blank": [], "gws_with_double": []} for t in team_ids}
    for gw, team_fixtures in fixtures_by_gw_team.items():
        for t in team_ids:
            fx = team_fixtures.get(t, [])
            if len(fx) == 0:
                swing[t]["gws_with_blank"].append(gw)
            elif len(fx) >= 2:
                swing[t]["gws_with_double"].append(gw)
            for _, _, fdr in fx:
                swing[t]["fdrs"].append(fdr)

    result = {}
    for t, s in swing.items():
        result[t] = {
            "avg_fdr": round(sum(s["fdrs"]) / len(s["fdrs"]), 2) if s["fdrs"] else None,
            "num_fixtures": len(s["fdrs"]),
            "gws_with_blank": s["gws_with_blank"],
            "gws_with_double": s["gws_with_double"],
        }
    return result


def squad_bench_boost_score(swing_by_team: dict, squad: list) -> float:
    """Fixture-DIFFICULTY ease only (6 - fdr, averaged) across the current squad's fixtures over
    the whole horizon - useful for wildcard timing (is this generally a good run?), but blind to
    fixture COUNT: a double at fdr 3 and a single at fdr 3 look identical to this metric. Do not
    use this to time bench-boost/free-hit - use squad_gw_fixture_counts for that.
    """
    fdrs = []
    for p in squad:
        team_swing = swing_by_team.get(p["team"])
        if team_swing and team_swing["avg_fdr"] is not None:
            fdrs.append(6 - team_swing["avg_fdr"])
    return round(sum(fdrs) / len(fdrs), 2) if fdrs else 0.0


def squad_gw_fixture_counts(swing_by_team: dict, squad: list, start_gw: int, num_gws: int) -> list:
    """Per gameweek in the horizon: which owned players double or blank, and the squad's total
    fixture count that week (15 = normal; higher = strong bench-boost candidate since bench
    players get extra fixtures too; lower = free-hit territory since starters have nothing to
    play). This is the actual DGW/BGW-relevant signal - squad_bench_boost_score's avg-fdr
    formula does not capture it (a double and a single at the same fdr score identically there).
    """
    summaries = []
    for gw in range(start_gw, start_gw + num_gws):
        doublers = [p["web_name"] for p in squad if gw in swing_by_team.get(p["team"], {}).get("gws_with_double", [])]
        blankers = [p["web_name"] for p in squad if gw in swing_by_team.get(p["team"], {}).get("gws_with_blank", [])]
        total_fixtures = len(squad) + len(doublers) - len(blankers)
        summaries.append({
            "gw": gw, "doublers": doublers, "blankers": blankers,
            "total_fixtures": total_fixtures, "squad_size": len(squad),
        })
    return summaries


def any_blanks_or_doubles(swing_by_team: dict) -> bool:
    return any(s["gws_with_blank"] or s["gws_with_double"] for s in swing_by_team.values())


def detect_stacking_opportunity(gw_fixture_counts: list, captain_team_id: int, swing_by_team: dict) -> list:
    """Gameweeks where a bench-boost-worthy fixture count AND the current captain's team having
    a double coincide - the classic advanced move (Bench Boost + Triple Captain the same week).
    Doesn't decide to play both by itself, same manual-reasoning pattern as the rest of this
    module - just surfaces where the combo is even on the table so it isn't missed.
    """
    return [
        s["gw"] for s in gw_fixture_counts
        if s["total_fixtures"] > s["squad_size"]
        and s["gw"] in swing_by_team.get(captain_team_id, {}).get("gws_with_double", [])
    ]


def chip_call(chip_key: str, action: str, confidence: str, rationale: str) -> dict:
    if action not in ("HOLD", "PLAY"):
        raise ValueError(f"invalid action: {action}")
    if confidence not in ("High", "Medium", "Low"):
        raise ValueError(f"invalid confidence: {confidence}")
    return {"chip": chip_key, "action": action, "confidence": confidence, "rationale": rationale}
