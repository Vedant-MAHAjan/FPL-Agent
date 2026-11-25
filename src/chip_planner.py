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
from wildcard import score_squad

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


# --- in-season chip values: what each chip is projected to ADD in each remaining gameweek ---

def per_gw_points(projections: list) -> dict:
    """gw -> {player id -> projected points that gameweek}, from build_multi_gw_projections output
    (each player's per_gw entries already sum both games in a double and are 0 in a blank)."""
    out = {}
    for p in projections:
        for g in p.get("per_gw", []):
            out.setdefault(g["gw"], {})[p["id"]] = g["points"]
    return out


def team_fixture_counts(fixtures: list, gws: range) -> dict:
    """gw -> {team id -> number of fixtures}. 0 = blank, 2+ = double. Teams absent have 0."""
    counts = {gw: {} for gw in gws}
    for f in fixtures:
        if f.get("event") in counts:
            for t in (f["team_h"], f["team_a"]):
                counts[f["event"]][t] = counts[f["event"]].get(t, 0) + 1
    return counts


def chip_values(projections: list, squad_ids: set, rules: dict, gws: range, free_hit_fn=None) -> list:
    """Per gameweek: bench boost = projected bench points of the best XI; triple captain = the
    captain's extra points; free hit = best one-week squad (free_hit_fn) minus own best XI."""
    by_gw = per_gw_points(projections)
    rows = []
    for gw in gws:
        pts = by_gw.get(gw, {})
        week = [{**p, "projected_points": pts.get(p["id"], 0.0)} for p in projections]
        own = score_squad(squad_ids, week, rules)
        xi_ids = {p["id"] for p in own["starting_xi"]}
        row = {
            "gw": gw,
            "own_xi": round(own["starting_xi_points"], 1),
            "bench_boost": round(sum(pts.get(i, 0.0) for i in squad_ids - xi_ids), 1),
            "triple_captain": round(own["captain"]["projected_points"], 1),
            "captain": own["captain"]["web_name"],
        }
        if free_hit_fn:
            row["free_hit"] = round(free_hit_fn(week) - own["starting_xi_points"], 1)
        rows.append(row)
    return rows


def main():
    import argparse
    import json
    import os
    from datetime import date

    import memory
    import opponent_strength
    import recent_form
    from fpl_api import get_bootstrap_static, get_fixtures
    from optimizer import optimize_squad, squad_rules_from_bootstrap
    from projections import build_multi_gw_projections, load_preseason_rate_baseline
    from transfer import _owned_context

    ap = argparse.ArgumentParser()
    ap.add_argument("gw", type=int, help="first gameweek to plan")
    ap.add_argument("--last", type=int, default=19, help="last gameweek the chips can be played (expiry)")
    args = ap.parse_args()
    gws = range(args.gw, args.last + 1)

    raw = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
    bootstrap, fixtures = get_bootstrap_static(), get_fixtures()
    snap = os.path.join(raw, f"bootstrap_static_{date.today().isoformat()}.json")
    with open(snap, "w") as f:
        json.dump(bootstrap, f)
    blended = recent_form.build_blended_baseline(bootstrap, load_preseason_rate_baseline(raw, snap))
    diff = opponent_strength.make_difficulty_fn(opponent_strength.build_team_ratings(fixtures, bootstrap),
                                                recent_form.completed_gameweeks(bootstrap))
    proj = build_multi_gw_projections(bootstrap, fixtures, start_gw=args.gw, num_gws=len(gws),
                                      rate_baseline=blended, difficulty_fn=diff)
    state = memory.get_squad_state()
    proj, owned, sell = _owned_context(proj, state)
    rules = squad_rules_from_bootstrap(bootstrap)

    def free_hit(week):
        return optimize_squad(week, rules, owned_ids=owned, sell_price_by_id=sell, bank=state["bank"],
                              max_transfers=rules["squad_size"])["starting_xi_points"]

    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    counts = team_fixture_counts(fixtures, gws)
    unscheduled = sum(1 for f in fixtures if f.get("event") is None)
    print(f"Chip values GW{gws.start}-{gws.stop - 1} for the current squad "
          f"(unscheduled fixtures still to be placed: {unscheduled})\n")
    print(f"{'GW':>3} {'own XI':>7} {'bench boost':>12} {'triple C':>9} {'free hit':>9}  captain / blanks / doubles")
    for row in chip_values(proj, owned, rules, gws, free_hit):
        c = counts[row["gw"]]
        blanks = [teams[t] for t in teams if c.get(t, 0) == 0]
        doubles = [teams[t] for t in teams if c.get(t, 0) >= 2]
        print(f"{row['gw']:>3} {row['own_xi']:>7} {row['bench_boost']:>12} {row['triple_captain']:>9} {row['free_hit']:>9}  "
              f"{row['captain']}{' | BLANK ' + ','.join(blanks) if blanks else ''}{' | DOUBLE ' + ','.join(doubles) if doubles else ''}")
    print("\nchips left:", [k for k, v in state["chips"].items() if v["available"]])


if __name__ == "__main__":
    main()
