"""Dry run of the weekly transfer decision against the real current squad_state.json. Prints
the recommendation across every transfer count considered - does NOT write anything back to
squad_state.json. Committing a transfer is a separate, deliberate action for when a real
gameweek decision is actually being made, not something a test run should do automatically.
"""

import glob
import json
import os

import memory
from fpl_api import get_fixtures
from optimizer import squad_rules_from_bootstrap
from projections import POSITION_NAMES, build_multi_gw_projections, load_preseason_rate_baseline
from transfer import best_transfer_plan, next_best_targets

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def latest_bootstrap_path() -> str:
    return sorted(glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json")))[-1]


def fmt(p, teams_by_id):
    return f"{POSITION_NAMES[p['element_type']]} {p['web_name']} ({teams_by_id[p['team']]}, £{p['now_cost']/10:.1f}m)"


def main():
    with open(latest_bootstrap_path()) as f:
        bootstrap = json.load(f)
    bootstrap_path = latest_bootstrap_path()
    fixtures = get_fixtures()
    teams_by_id = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    state = memory.get_squad_state()
    decision_gw = state["as_of_gw"] + 1

    rate_baseline = load_preseason_rate_baseline(RAW_DIR, bootstrap_path)
    projections = build_multi_gw_projections(
        bootstrap, fixtures, start_gw=decision_gw, num_gws=3, rate_baseline=rate_baseline
    )
    rules = squad_rules_from_bootstrap(bootstrap)

    plan = best_transfer_plan(projections, rules, state, free_transfers=state["free_transfers"])

    print("=" * 90)
    print(f"TRANSFER DECISION — GW{decision_gw}  (free transfers available: {state['free_transfers']}, "
          f"bank: £{state['bank']/10:.1f}m)")
    print("=" * 90)

    print("\nAll transfer counts considered:")
    for c in plan["all_candidates"]:
        marker = "  <== BEST" if c is plan["best"] else ""
        print(f"  {c['num_transfers']} transfer(s): gross={c['gross_points']:.1f}  "
              f"hit=-{c['hit']}  net={c['net_score']:.1f}{marker}")

    best = plan["best"]
    print(f"\nRECOMMENDATION: {best['num_transfers']} transfer(s)"
          + (f" (takes a -{best['hit']} hit)" if best["hit"] else " (no hit)"))

    if best["num_transfers"] == 0:
        print("No transfer is worth making this week - the squad you already own is still optimal.")
    else:
        outs = sorted(best["result"]["transfers_out"], key=lambda p: p["element_type"])
        ins = sorted(best["result"]["transfers_in"], key=lambda p: p["element_type"])
        print("\nOUT -> IN:")
        for out_p, in_p in zip(outs, ins):
            print(f"  OUT {fmt(out_p, teams_by_id):40s} -> IN {fmt(in_p, teams_by_id)}")

    print("\n" + "=" * 90)
    print("WATCHLIST — top targets not currently owned, by points-per-million")
    print("=" * 90)
    for t in next_best_targets(projections, state, n=8):
        afford = "affordable now" if t["affordable_now"] else f"short by £{t['shortfall']/10:.1f}m"
        print(f"  {POSITION_NAMES[t['element_type']]:3s} {t['web_name']:16s} £{t['now_cost']/10:.1f}m  "
              f"proj={t['projected_points']:.1f}  ppm={t['points_per_million']:.2f}  ({afford})")

    print(f"\n(dry run only - squad_state.json was not modified)")


if __name__ == "__main__":
    main()
