"""Backtest for the clean-sheet model: which way of estimating a defence's goals conceded best
predicts what actually happened? For each past gameweek it rebuilds the projections from the
bootstrap snapshot saved before that deadline (data/raw/), using only results known at the time,
then scores goalkeepers and defenders who played 60+ minutes against their real outcome:

  - Brier score of the predicted clean-sheet probability vs whether they kept one (lower = better)
  - mean absolute error of projected vs actual gameweek points (lower = better)

Run: python src/backtest_clean_sheets.py
"""

import glob
import json
import os

import opponent_strength
import projections as proj
import recent_form
from fpl_api import get_event_live, get_fixtures

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
VARIANTS = [  # (label, XGC_WEIGHT, CS_FROM_CONCEDED_RATE)
    ("old: actual goals, raw clean-sheet rate", 0.0, False),
    ("actual goals, Poisson", 0.0, True),
    ("30% chance quality", 0.3, True),
    ("50% chance quality (chosen)", 0.5, True),
    ("70% chance quality", 0.7, True),
    ("100% chance quality", 1.0, True),
]


def _snapshot_before(deadline: str) -> str | None:
    """Latest snapshot dated on/before the deadline's day (snapshots are taken pre-deadline)."""
    day = deadline[:10]
    paths = sorted(p for p in glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json"))
                   if os.path.basename(p)[len("bootstrap_static_"):-len(".json")] <= day)
    return paths[-1] if paths else None


def _gw_samples(gw, bootstrap, fixtures, snap_path, live):
    preseason = proj.load_preseason_rate_baseline(RAW_DIR, snap_path)
    blended = recent_form.build_blended_baseline(bootstrap, preseason)
    known = [f for f in fixtures if f.get("event") and f["event"] < gw]
    ratings = opponent_strength.build_team_ratings(known, bootstrap)
    difficulty_fn = opponent_strength.make_difficulty_fn(ratings, gw - 1)
    scoring = proj.load_scoring_rules(bootstrap)
    projected = proj.build_multi_gw_projections(bootstrap, fixtures, start_gw=gw, num_gws=1,
                                                rate_baseline=blended, difficulty_fn=difficulty_fn)
    out = []
    for p in projected:
        stats = live.get(p["id"])
        if p["element_type"] not in (1, 2) or not stats or stats["minutes"] < 60 or not p["per_gw"]:
            continue
        if len(p["per_gw"][0]["fixtures"]) != 1:
            continue  # blank/double weeks: one clean-sheet probability doesn't map to one outcome
        minute_frac = p["expected_minutes"] / 90
        if minute_frac <= 0:
            continue
        cs_prob = p["breakdown_per_fixture"]["clean_sheet"] / (scoring["clean_sheets"][p["element_type"]] * minute_frac)
        out.append((min(1.0, cs_prob), stats["clean_sheets"], p["projected_points"], stats["total_points"]))
    return out


def main():
    fixtures = get_fixtures()
    with open(sorted(glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json")))[-1]) as f:
        events = {e["id"]: e for e in json.load(f)["events"]}
    gws = [g for g, e in events.items() if g >= 2 and e.get("finished")]

    inputs = []
    for gw in gws:
        snap = _snapshot_before(events[gw]["deadline_time"])
        if snap is None:
            continue
        with open(snap) as f:
            bootstrap = json.load(f)
        live = {e["id"]: e["stats"] for e in get_event_live(gw)["elements"]}
        inputs.append((gw, bootstrap, snap, live))
        print(f"GW{gw}: snapshot {os.path.basename(snap)}")

    print(f"\n{'variant':42} {'n':>4} {'Brier (CS)':>11} {'MAE (pts)':>10}")
    saved = proj.XGC_WEIGHT, proj.CS_FROM_CONCEDED_RATE
    try:
        for label, weight, poisson in VARIANTS:
            proj.XGC_WEIGHT, proj.CS_FROM_CONCEDED_RATE = weight, poisson
            samples = [s for gw, b, snap, live in inputs for s in _gw_samples(gw, b, fixtures, snap, live)]
            brier = sum((p - cs) ** 2 for p, cs, _, _ in samples) / len(samples)
            mae = sum(abs(pp - ap) for _, _, pp, ap in samples) / len(samples)
            print(f"{label:42} {len(samples):4} {brier:11.4f} {mae:10.3f}")
        # Reference: predict the same clean-sheet chance (the observed rate) for everyone. A model
        # that can't beat this has no real read on which defences will keep clean sheets.
        rate = sum(cs for _, cs, _, _ in samples) / len(samples)
        print(f"{'reference: same chance for everyone':42} {len(samples):4} {rate * (1 - rate):11.4f} {'-':>10}"
              f"   (observed clean-sheet rate {rate:.0%})")
    finally:
        proj.XGC_WEIGHT, proj.CS_FROM_CONCEDED_RATE = saved


if __name__ == "__main__":
    main()
