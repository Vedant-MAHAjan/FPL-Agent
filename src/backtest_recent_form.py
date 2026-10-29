"""Backtest for recent-form weighting: should attacking projections lean toward a player's last few
gameweeks, or trust the season-long rate? For each past gameweek it rebuilds projections from the
bootstrap snapshot saved before that deadline, with recent form taken only from the RECENT_GWS
gameweeks before it, then scores midfielders and forwards who played 60+ minutes:

  - mean absolute error of projected vs actual points (lower = better)
  - rank correlation of projected vs actual points (higher = better: did it order players right?)

Run: python src/backtest_recent_form.py
"""

import glob
import json
import os

import opponent_strength
import projections as proj
import recent_form
from fpl_api import get_bootstrap_static, get_event_live, get_fixtures

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
WEIGHTS = [0.0, 0.25, 0.5, 0.75, 1.0]


def _snapshot_before(deadline: str) -> str | None:
    day = deadline[:10]
    paths = sorted(p for p in glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json"))
                   if os.path.basename(p)[len("bootstrap_static_"):-len(".json")] <= day)
    return paths[-1] if paths else None


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for rank, i in enumerate(order):
        r[i] = rank
    return r


def _spearman(a, b):
    ra, rb = _ranks(a), _ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb)


def main():
    fixtures = get_fixtures()
    events = {e["id"]: e for e in get_bootstrap_static()["events"]}  # live: snapshots predate later results
    window = recent_form.RECENT_GWS
    gws = [g for g, e in events.items() if g > window and e.get("finished")]
    lives = {g: get_event_live(g) for g in range(1, max(gws) + 1)}

    inputs = []
    for gw in gws:
        snap = _snapshot_before(events[gw]["deadline_time"])
        if snap is None:
            continue
        with open(snap) as f:
            bootstrap = json.load(f)
        recent = recent_form.recent_attacking_rates([lives[g] for g in range(gw - window, gw)])
        actual = {e["id"]: e["stats"] for e in lives[gw]["elements"]}
        inputs.append((gw, bootstrap, snap, recent, actual))
        print(f"GW{gw}: snapshot {os.path.basename(snap)}, recent window GW{gw - window}-{gw - 1}")

    # Reference: FPL's own published expected points (ep_next) from the same pre-deadline snapshot.
    ref_p, ref_a = [], []
    for gw, bootstrap, snap, recent, actual in inputs:
        for e in bootstrap["elements"]:
            st = actual.get(e["id"])
            if e["element_type"] in (3, 4) and st and st["minutes"] >= 60:
                ref_p.append(float(e["ep_next"] or 0))
                ref_a.append(st["total_points"])
    print(f"\nreference - FPL's own expected points: n {len(ref_p)}, "
          f"MAE {sum(abs(a - b) for a, b in zip(ref_p, ref_a)) / len(ref_p):.3f}, rank corr {_spearman(ref_p, ref_a):.3f}")
    print(f"\n{'recent weight':>13} {'n':>5} {'MAE (pts)':>10} {'rank corr':>10}")
    saved = recent_form.RECENT_WEIGHT
    try:
        for w in WEIGHTS:
            recent_form.RECENT_WEIGHT = w
            proj_pts, act_pts = [], []
            for gw, bootstrap, snap, recent, actual in inputs:
                pre = proj.load_preseason_rate_baseline(RAW_DIR, snap)
                blended = recent_form.build_blended_baseline(bootstrap, pre, recent=recent)
                known = [f for f in fixtures if f.get("event") and f["event"] < gw]
                diff = opponent_strength.make_difficulty_fn(opponent_strength.build_team_ratings(known, bootstrap), gw - 1)
                for p in proj.build_multi_gw_projections(bootstrap, fixtures, start_gw=gw, num_gws=1,
                                                         rate_baseline=blended, difficulty_fn=diff):
                    st = actual.get(p["id"])
                    if p["element_type"] in (3, 4) and st and st["minutes"] >= 60:
                        proj_pts.append(p["projected_points"])
                        act_pts.append(st["total_points"])
            mae = sum(abs(a - b) for a, b in zip(proj_pts, act_pts)) / len(proj_pts)
            print(f"{w:13.2f} {len(proj_pts):5} {mae:10.3f} {_spearman(proj_pts, act_pts):10.3f}")
    finally:
        recent_form.RECENT_WEIGHT = saved


if __name__ == "__main__":
    main()
