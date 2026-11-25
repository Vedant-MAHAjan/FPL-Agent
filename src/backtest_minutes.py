"""Backtest for minutes risk: should expected minutes lean on a player's last few gameweeks of actual
minutes, so someone dropping out of the side is projected lower sooner? For each past gameweek it
rebuilds projections from the bootstrap snapshot saved before that deadline, with recent minutes
taken only from the WINDOW gameweeks before it, then scores every player the baseline model gave at
least MIN_PROJ points - including the ones who then didn't play, which is the case being tested:

  - mean absolute error of projected vs actual points (lower = better)
  - rank correlation of projected vs actual points (higher = better)
  - error on players who played 0 minutes (how much the model over-credits benched players)

Run: python src/backtest_minutes.py
"""

import glob
import json
import os

import opponent_strength
import projections as proj
import recent_form
from backtest_recent_form import _snapshot_before, _spearman
from fpl_api import get_bootstrap_static, get_event_live, get_fixtures

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
WEIGHTS = [0.0, 0.25, 0.5, 0.75, 1.0]
WINDOW = 3
MIN_PROJ = 2.0


def main():
    fixtures = get_fixtures()
    events = {e["id"]: e for e in get_bootstrap_static()["events"]}
    gws = [g for g, e in events.items() if g > WINDOW and e.get("finished")]
    lives = {g: get_event_live(g) for g in range(1, max(gws) + 1)}

    inputs = []
    for gw in gws:
        snap = _snapshot_before(events[gw]["deadline_time"])
        if snap is None:
            continue
        with open(snap) as f:
            bootstrap = json.load(f)
        mins = recent_form.recent_minutes([lives[g] for g in range(gw - WINDOW, gw)])
        actual = {e["id"]: e["stats"] for e in lives[gw]["elements"]}
        inputs.append((gw, bootstrap, snap, mins, actual))
        print(f"GW{gw}: snapshot {os.path.basename(snap)}, minutes window GW{gw - WINDOW}-{gw - 1}")

    def run(weight):
        recent_form.RECENT_MINUTES_WEIGHT = weight
        out = {}
        for gw, bootstrap, snap, mins, actual in inputs:
            blended = recent_form.build_blended_baseline(bootstrap, proj.load_preseason_rate_baseline(RAW_DIR, snap),
                                                         recent_mins=mins, recent_gws=WINDOW)
            known = [f for f in fixtures if f.get("event") and f["event"] < gw]
            diff = opponent_strength.make_difficulty_fn(opponent_strength.build_team_ratings(known, bootstrap), gw - 1)
            for p in proj.build_multi_gw_projections(bootstrap, fixtures, start_gw=gw, num_gws=1,
                                                     rate_baseline=blended, difficulty_fn=diff):
                st = actual.get(p["id"])
                if st is not None:
                    out[(gw, p["id"])] = (p["projected_points"], st["total_points"], st["minutes"])
        return out

    saved = recent_form.RECENT_MINUTES_WEIGHT
    try:
        base = run(0.0)
        population = {k for k, (pp, _, _) in base.items() if pp >= MIN_PROJ}  # fixed across variants
        print(f"\n{'minutes weight':>14} {'n':>5} {'MAE':>7} {'rank corr':>10} {'n benched':>10} {'MAE benched':>12}")
        for w in WEIGHTS:
            res = base if w == 0.0 else run(w)
            rows = [res[k] for k in population if k in res]
            benched = [r for r in rows if r[2] == 0]
            mae = sum(abs(pp - ap) for pp, ap, _ in rows) / len(rows)
            mae_b = sum(abs(pp - ap) for pp, ap, _ in benched) / len(benched) if benched else float("nan")
            corr = _spearman([r[0] for r in rows], [r[1] for r in rows])
            print(f"{w:14.2f} {len(rows):5} {mae:7.3f} {corr:10.3f} {len(benched):10} {mae_b:12.3f}")
    finally:
        recent_form.RECENT_MINUTES_WEIGHT = saved


if __name__ == "__main__":
    main()
