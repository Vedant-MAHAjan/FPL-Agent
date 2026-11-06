"""Backtest for captain choice: should the captain be the starter with the best NEXT-gameweek
projection, or the best total over the 3-gameweek horizon (what the optimizer picks today)? For each
past gameweek it rebuilds projections from the bootstrap snapshot saved before that deadline, takes
the XI actually played, and compares each rule's captain against what he really scored.

Result 2026-11-06 (GW2-9): the rules agreed in 7 of 8 weeks; the one disagreement (GW9) went to
the 3-gameweek pick, 66 vs 62 captain points in total. No evidence for switching, so the optimizer's
3-gameweek captain stays.

Run: python src/backtest_captain.py [entry_id]
"""

import glob
import json
import os
import sys

import opponent_strength
import projections as proj
import recent_form
from fpl_api import get_bootstrap_static, get_entry_picks, get_event_live, get_fixtures

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
DEFAULT_ENTRY = 2360640


def _snapshot_before(deadline: str) -> str | None:
    day = deadline[:10]
    paths = sorted(p for p in glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json"))
                   if os.path.basename(p)[len("bootstrap_static_"):-len(".json")] <= day)
    return paths[-1] if paths else None


def main():
    entry = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ENTRY
    fixtures = get_fixtures()
    events = {e["id"]: e for e in get_bootstrap_static()["events"]}
    totals = {"next gameweek": 0, "3-gameweek total": 0, "hindsight best": 0}
    for gw in sorted(g for g, e in events.items() if g >= 2 and e.get("finished")):
        snap = _snapshot_before(events[gw]["deadline_time"])
        if snap is None:
            continue
        with open(snap) as f:
            bootstrap = json.load(f)
        names = {e["id"]: e["web_name"] for e in bootstrap["elements"]}
        blended = recent_form.build_blended_baseline(bootstrap, proj.load_preseason_rate_baseline(RAW_DIR, snap))
        known = [f for f in fixtures if f.get("event") and f["event"] < gw]
        diff = opponent_strength.make_difficulty_fn(opponent_strength.build_team_ratings(known, bootstrap), gw - 1)

        def horizon(n):
            return {p["id"]: p["projected_points"] for p in proj.build_multi_gw_projections(
                bootstrap, fixtures, start_gw=gw, num_gws=n, rate_baseline=blended, difficulty_fn=diff)}

        next_gw, three_gw = horizon(1), horizon(3)
        xi = [p["element"] for p in get_entry_picks(entry, gw)["picks"] if p["position"] <= 11]
        actual = {e["id"]: e["stats"]["total_points"] for e in get_event_live(gw)["elements"]}
        picks = {"next gameweek": max(xi, key=lambda i: next_gw.get(i, 0)),
                 "3-gameweek total": max(xi, key=lambda i: three_gw.get(i, 0)),
                 "hindsight best": max(xi, key=lambda i: actual.get(i, 0))}
        for rule, pid in picks.items():
            totals[rule] += actual.get(pid, 0)
        print(f"GW{gw}: " + " | ".join(f"{rule} -> {names.get(pid, pid)} {actual.get(pid, 0)}" for rule, pid in picks.items()))
    print(f"\ncaptain points (single, before doubling): {totals}")


if __name__ == "__main__":
    main()
