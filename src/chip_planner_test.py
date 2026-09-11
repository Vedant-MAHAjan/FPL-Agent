"""First real run of the chip planner: current preseason state (GW1, all 8 chip-halves
available, squad just picked), reasoned over a GW1-6 fixture window. Analogous to
judgment_pass.py's digest-driven pattern - real inputs, hand-reasoned output, not a black box.
"""

import glob
import json
import os

import memory
from chip_planner import (
    CHIP_PLANNING_HORIZON,
    any_blanks_or_doubles,
    chip_call,
    detect_stacking_opportunity,
    squad_bench_boost_score,
    squad_gw_fixture_counts,
    team_fixture_swing,
)
from fpl_api import get_fixtures

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def latest_bootstrap_path() -> str:
    return sorted(glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json")))[-1]


def main():
    with open(latest_bootstrap_path()) as f:
        bootstrap = json.load(f)
    fixtures = get_fixtures()
    teams_by_id = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    state = memory.get_squad_state()
    as_of_gw = state["as_of_gw"]
    available = memory.remaining_chips(state, as_of_gw)

    swing = team_fixture_swing(fixtures, bootstrap["teams"], as_of_gw, CHIP_PLANNING_HORIZON)
    bb_score = squad_bench_boost_score(swing, state["squad"])
    has_blank_or_double = any_blanks_or_doubles(swing)
    gw_fixture_counts = squad_gw_fixture_counts(swing, state["squad"], as_of_gw, CHIP_PLANNING_HORIZON)

    print("=" * 90)
    print(f"CHIP PLANNER — as of GW{as_of_gw}, horizon GW{as_of_gw}-{as_of_gw + CHIP_PLANNING_HORIZON - 1}")
    print("=" * 90)
    print(f"\nChips still available in-window: {available}")
    print(f"Blank/double gameweeks detected in horizon: {has_blank_or_double}")
    print(f"Squad fixture-count by gameweek (15 = normal; this is the number that actually "
          f"matters for bench-boost/free-hit timing, not the fdr-ease score below):")
    for s in gw_fixture_counts:
        flag = ""
        if s["total_fixtures"] > 15:
            flag = f"  <-- bench-boost candidate ({', '.join(s['doublers'])} double)"
        elif s["total_fixtures"] < 15:
            flag = f"  <-- free-hit candidate ({', '.join(s['blankers'])} blank)"
        print(f"  GW{s['gw']}: {s['total_fixtures']}/15{flag}")
    print(f"\nCurrent squad's fixture-EASE score (fdr-based, for wildcard timing not bboost/freehit): "
          f"{bb_score} (higher = easier run; no baseline yet to compare against, this is the first "
          f"reading of the season)")

    print("\nEasiest fixture runs (top 5 by avg FDR, lower = easier):")
    ranked = sorted((t for t in swing.items() if t[1]["avg_fdr"] is not None), key=lambda kv: kv[1]["avg_fdr"])
    for team_id, s in ranked[:5]:
        print(f"  {teams_by_id[team_id]:4s} avg_fdr={s['avg_fdr']}  fixtures={s['num_fixtures']}")

    # squad_state tracks the owned squad but not who's currently captained - use the priciest
    # squad player as a proxy for "who's most likely captain" until that's threaded through.
    captain_team_id = max(state["squad"], key=lambda p: p["purchase_price"])["team"]
    stacking_gws = detect_stacking_opportunity(gw_fixture_counts, captain_team_id, swing)
    if stacking_gws:
        print(f"\nSTACKING OPPORTUNITY: GW{stacking_gws} - bench-boost-worthy fixture count AND "
              "your likely captain's team doubling coincide. Bench Boost + Triple Captain the "
              "same week is the advanced move here if both chips are still available then.")
    else:
        print("\nNo bench-boost + triple-captain stacking opportunity in this window.")

    # chip keys come from FPL's own `chips[].name` field (see memory.chip_windows_from_bootstrap):
    # wildcard, freehit, bboost, 3xc - not friendlier guessed names.
    calls = []
    if "wildcard_1" in available:
        calls.append(chip_call(
            "wildcard_1", "HOLD", "High",
            "Squad was just built fresh by the optimizer this week - there is nothing to "
            "correct yet. Standard practice (and the whole point of holding a wildcard) is to "
            "save it for when squad value/injuries/fixture swings actually require a rebuild, "
            "not spend it on week one."
        ))
    if "bboost_1" in available:
        max_fixtures = max(s["total_fixtures"] for s in gw_fixture_counts)
        # squad_state.squad is stored as starting_xi + bench (see memory.init_squad_state), so the
        # last 4 entries are always the bench.
        bench_names = "/".join(p["web_name"] for p in state["squad"][11:])
        calls.append(chip_call(
            "bboost_1", "HOLD", "High",
            f"No gameweek in the GW{as_of_gw}-{as_of_gw + CHIP_PLANNING_HORIZON - 1} window beats "
            f"the 15/15 baseline (best seen: {max_fixtures}/15) - no doubles have hit the squad, "
            "which is expected this early (doubles/blanks get created by mid-season cup "
            f"reschedules, not by GW6). The bench itself ({bench_names}) is also cheap squad-value "
            "cover today, not a bench built to be played - re-run this check once a real double "
            "gameweek is announced for this squad's teams."
        ))
    if "3xc_1" in available:
        calls.append(chip_call(
            "3xc_1", "HOLD", "Medium",
            "Haaland is the clear captain candidate long-term, but step 3's judgment check found "
            "real preseason uncertainty around his fitness/rotation under Man City's new manager "
            "- exactly the wrong week to triple a pick that's already flagged Medium (not High) "
            "confidence. Worth revisiting once he's had a settled run of starts."
        ))
    if "freehit_1" in available:
        calls.append(chip_call(
            "freehit_1", "HOLD", "High",
            "No blank or double gameweeks detected in the GW1-6 window (confirmed by "
            "any_blanks_or_doubles() against live fixture data) - free hit exists specifically "
            "for those, so there's currently no target to use it on."
        ))

    print(f"\n--- Chip calls ({len(calls)}) ---")
    for c in calls:
        print(f"\n{c['chip']}: {c['action']} — {c['confidence']}")
        print(f"  {c['rationale']}")

    record = memory.log_chip_decision(as_of_gw, calls)
    print(f"\n\nSaved -> {memory.CHIP_DECISIONS_PATH}")


if __name__ == "__main__":
    main()
