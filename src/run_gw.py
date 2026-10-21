"""One-command gameweek pipeline. Runs the whole deterministic pass for GW N and prints a brief
for the judgment conversation. Everything except the judgment call is automated here - resolve
the previous gameweek, snapshot data, sync the real squad, project, compute the numeric baseline
(best transfer + XI + captain), and scan the pool - so the chat stays about football, not
plumbing. Nothing about the final decision is logged until we actually decide it.

    python src/run_gw.py 4            # plan GW4 for the default entry
"""
import argparse
import json
import os
from datetime import date

import memory
import opponent_strength
import recent_form
import squad_sync
from fpl_api import get_bootstrap_static, get_element_summary, get_fixtures
from optimizer import squad_rules_from_bootstrap
from projections import build_multi_gw_projections, load_preseason_rate_baseline
from scan import scan_pool, log_scan, format_scan, template_gap, format_template_gap
from transfer import best_transfer_plan, wildcard_plan

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
DEFAULT_ENTRY = 2360640


def _snapshot(bootstrap):
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, f"bootstrap_static_{date.today().isoformat()}.json")
    with open(path, "w") as f:
        json.dump(bootstrap, f)
    return path


def _dead_slots(squad, el):
    """Owned players who can't help: gone from the game, unavailable, or not playing (0 minutes in
    2+ of their last 3 gameweeks). Obi (loaned out) and Sessegnon (unused) sat unflagged for weeks."""
    dead = []
    for p in squad:
        e = el.get(p["id"])
        if e is None:
            dead.append((p.get("web_name", p["id"]), "left the game"))
            continue
        if e["status"] in ("u", "n"):
            dead.append((e["web_name"], e["news"] or "unavailable"))
            continue
        recent = get_element_summary(p["id"])["history"][-3:]
        zero = sum(1 for h in recent if h["minutes"] == 0)
        if len(recent) >= 2 and zero >= 2:
            dead.append((e["web_name"], f"0 minutes in {zero} of last {len(recent)}"))
    return dead


def _resolve_previous(gw):
    # Every unresolved past decision, not just gw-1: a skipped week would otherwise never be
    # scored and silently drop out of the season comparison.
    pending = sorted({r["gw"] for r in memory._load_all_decisions() if not r["resolved"] and r["gw"] < gw})
    if not pending:
        print("  nothing pending")
    for prev in pending:
        try:
            rec = memory.record_outcome(prev)
            o = rec["outcome"]
            print(f"  GW{prev} resolved: {o['starting_xi_total']} pts "
                  f"(baseline {o['baseline_starting_xi_total']}, judgment delta {o['judgment_delta']:+d})")
            if o.get("lineup_followed") is False:
                print(f"  !! GW{prev}: the lineup actually played differed from the recommendation "
                      f"(real score {o['actual_points']}) - the scoreboard grades the recommendation.")
        except (KeyError, RuntimeError) as e:
            print(f"  GW{prev} not resolved yet ({type(e).__name__}) - not finalized")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gw", type=int)
    ap.add_argument("--entry", type=int, default=DEFAULT_ENTRY)
    ap.add_argument("--ft", type=int, default=None,
                    help="Override the free-transfer count (the API doesn't expose it; confirm from the app).")
    args = ap.parse_args()
    gw = args.gw
    print(f"\n{'='*72}\n GW{gw} PIPELINE\n{'='*72}")

    print("\n[1] Resolve previous gameweek + calibration")
    _resolve_previous(gw)
    cal = memory.compute_calibration()
    comp = memory.compute_season_comparison()
    print(f"  calibration by tier: {json.dumps(cal.get('by_confidence', {}))}")
    print(f"  overrides by support: {json.dumps(cal.get('overrides_by_support', {}))}")
    if comp.get("delta") is not None:
        print(f"  season judgment vs baseline: {comp['delta']:+d} pts "
              f"({comp['judgment_wins']}W / {comp['ties']}T / {comp['baseline_wins']}L)")

    print("\n[2] Snapshot data")
    bootstrap = get_bootstrap_static()
    fixtures = get_fixtures()
    snap = _snapshot(bootstrap)
    el = {e["id"]: e for e in bootstrap["elements"]}
    print(f"  saved {os.path.basename(snap)}")

    print("\n[3] Sync squad from API")
    state = squad_sync.sync_squad_state(args.entry)
    owned_ids = {p["id"] for p in state["squad"]}
    derived_ft = state["free_transfers"]
    free_transfers = args.ft if args.ft is not None else derived_ft
    src = "OVERRIDE via --ft" if args.ft is not None else "derived (chip-aware, best-effort)"
    print(f"  as of GW{state['as_of_gw']} · bank £{state['bank']/10:.1f}m")
    print(f"  !! FREE TRANSFERS = {free_transfers}  [{src}] -- the API does NOT expose FT; "
          f"CONFIRM against the FPL app before trusting the hit math. Re-run with --ft N to correct.")
    print(f"  {', '.join(el[p['id']]['web_name'] for p in state['squad'])}")
    dead = _dead_slots(state["squad"], el)
    for name, why in dead:
        print(f"  !! DEAD SLOT: {name} - {why}")

    print("\n[4] Projections (in-season blend, form-adjusted fixtures) + numeric baseline")
    preseason = load_preseason_rate_baseline(RAW_DIR, snap)
    blended = recent_form.build_blended_baseline(bootstrap, preseason)
    completed = recent_form.completed_gameweeks(bootstrap)
    ratings = opponent_strength.build_team_ratings(fixtures, bootstrap)
    difficulty_fn = opponent_strength.make_difficulty_fn(ratings, completed)
    print(f"  projections: this-season rates blended vs last-season prior ({completed} GWs live); "
          f"fixture difficulty = static FDR blended with live form "
          f"(form weight {opponent_strength.form_weight(completed):.0%}, {ratings['n_fixtures']} results)")
    projections = build_multi_gw_projections(
        bootstrap, fixtures, start_gw=gw, num_gws=3, rate_baseline=blended, difficulty_fn=difficulty_fn)
    rules = squad_rules_from_bootstrap(bootstrap)
    plan = best_transfer_plan(projections, rules, state, free_transfers)
    best = plan["best"]
    r = best["result"]
    tin = {p["id"] for p in r["squad"]} - owned_ids
    tout = owned_ids - {p["id"] for p in r["squad"]}
    print(f"  numeric recommends {best['num_transfers']} transfer(s) (hit {best['hit']}, net {best['net_score']:.1f}):")
    own = lambda i: f"{el[i]['web_name']} ({float(el[i]['selected_by_percent']):.0f}%)"  # noqa: E731
    if tout:
        print(f"    OUT {', '.join(own(i) for i in tout)}  ->  IN {', '.join(own(i) for i in tin)}")
    else:
        print("    (bank - no transfer beats holding)")
    print(f"    C {own(r['captain']['id'])}  ·  VC {own(r['vice_captain']['id'])}")
    wc = wildcard_plan(projections, rules, state, plan)
    print(f"  wildcard check: best squad reachable makes {wc['num_transfers']} change(s), "
          f"{wc['gain_vs_best_regular']:+.1f} pts over the plan above (3-GW horizon)")

    print("\n[5] Full-pool scan (whole pool, nailed, by fixture-adjusted projection)")
    projections_by_id = {p["id"]: p for p in projections}
    scan = scan_pool(bootstrap, fixtures, owned_ids, start_gw=gw, projections_by_id=projections_by_id)
    scan_path = log_scan(scan, gw)
    print(format_scan(scan))
    print(f"\n  logged -> {os.path.relpath(scan_path)}")

    print("\n[6] Popular players you don't own (25%+ owned, fit, by projection)")
    print(format_template_gap(template_gap(bootstrap, owned_ids, projections_by_id)))

    print(f"\n{'='*72}\n READY FOR JUDGMENT - discuss the pick above. Nothing is logged until we decide.\n{'='*72}")


if __name__ == "__main__":
    main()
