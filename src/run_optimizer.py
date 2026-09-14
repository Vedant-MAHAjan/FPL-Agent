"""Numeric baseline layer entry point: pull latest data, project points over a short
multi-gameweek horizon, and solve for the optimal squad. This is the deterministic layer
only - no LLM/judgment involved.
"""

import glob
import json
import os

from fpl_api import get_fixtures
from optimizer import optimize_squad, squad_rules_from_bootstrap
from projections import (
    DEFAULT_HORIZON,
    DEFAULT_START_GW,
    POSITION_NAMES,
    build_multi_gw_projections,
    load_preseason_rate_baseline,
    top_differentials,
)

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")


def latest_bootstrap_path() -> str:
    candidates = sorted(glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json")))
    if not candidates:
        raise FileNotFoundError("No bootstrap_static_*.json found in data/raw - run fetch_bootstrap.py first.")
    return candidates[-1]


def fixture_run_str(p, teams_by_id):
    parts = []
    for gw_entry in p["per_gw"]:
        if not gw_entry["fixtures"]:
            parts.append(f"GW{gw_entry['gw']}:BLANK")
            continue
        legs = []
        for fx in gw_entry["fixtures"]:
            venue = "H" if fx["was_home"] else "A"
            legs.append(f"{teams_by_id[fx['opponent']]}({venue},fdr{fx['fdr']})")
        parts.append(f"GW{gw_entry['gw']}:" + "+".join(legs))
    return " ".join(parts)


def fmt_player(p, teams_by_id):
    cost = p["now_cost"] / 10
    team = teams_by_id[p["team"]]
    flag = " [LOW-CONF: no PL track record]" if p.get("low_confidence") else ""
    return (
        f"{POSITION_NAMES[p['element_type']]:3s} {p['web_name']:18s} {team:4s} "
        f"£{cost:.1f}m  proj={p['projected_points']:.2f}  {fixture_run_str(p, teams_by_id)}{flag}"
    )


def main():
    bootstrap_path = latest_bootstrap_path()
    with open(bootstrap_path) as f:
        bootstrap = json.load(f)
    print(f"Loaded {bootstrap_path}")

    fixtures = get_fixtures()
    teams_by_id = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    start_gw, num_gws = DEFAULT_START_GW, DEFAULT_HORIZON
    end_gw = start_gw + num_gws - 1
    rate_baseline = load_preseason_rate_baseline(RAW_DIR, bootstrap_path)
    projections = build_multi_gw_projections(
        bootstrap, fixtures, start_gw=start_gw, num_gws=num_gws, rate_baseline=rate_baseline
    )
    rules = squad_rules_from_bootstrap(bootstrap)
    result = optimize_squad(projections, rules)

    print()
    print("=" * 90)
    print(f"OPTIMAL SQUAD FOR GW{start_gw}-{end_gw}  (budget used: £{result['total_cost']/10:.1f}m / £{rules['budget']/10:.1f}m)")
    print("=" * 90)
    print("\nSTARTING XI:")
    for p in sorted(result["starting_xi"], key=lambda p: p["element_type"]):
        tag = ""
        if p["id"] == result["captain"]["id"]:
            tag = "  (C)"
        elif p["id"] == result["vice_captain"]["id"]:
            tag = "  (VC)"
        print("  " + fmt_player(p, teams_by_id) + tag)

    print("\nBENCH (order):")
    for p in result["bench"]:
        print("  " + fmt_player(p, teams_by_id))

    print(f"\nCaptain: {result['captain']['web_name']}  |  Vice: {result['vice_captain']['web_name']}")
    print(f"Projected starting-XI points over GW{start_gw}-{end_gw} (captain doubled each week): {result['starting_xi_points']:.1f}")

    low_conf = [p for p in result["squad"] if p.get("low_confidence")]
    if low_conf:
        print(f"\n{len(low_conf)} squad player(s) flagged low-confidence (no PL track record, neutral guess used):")
        for p in low_conf:
            print("  - " + p["web_name"])

    europe = [p for p in result["squad"] if p.get("european_competition")]
    if europe:
        print(f"\n{len(europe)} squad player(s) at clubs in Europe this season (real fixture-congestion "
              "rotation risk not captured by the projection - worth a manual news check nearer the deadline):")
        for p in europe:
            print(f"  - {p['web_name']} ({teams_by_id[p['team']]}, {p['european_competition']})")

    print(f"\nTop differentials (proj. points among <10% owned - not selected by the optimizer, "
          "which only maximizes raw points, but worth knowing for rank-chasing):")
    for p in top_differentials(projections, max_ownership=10.0, n=5):
        print(f"  {fmt_player(p, teams_by_id)}  (owned by {p['selected_by_percent']}%)")

    os.makedirs(PROCESSED_DIR, exist_ok=True)
    tag = f"gw{start_gw}-{end_gw}"
    out_path = os.path.join(PROCESSED_DIR, f"squad_{tag}.json")
    with open(out_path, "w") as f:
        json.dump(
            {
                "start_gw": start_gw,
                "end_gw": end_gw,
                "captain": result["captain"],
                "vice_captain": result["vice_captain"],
                "total_cost": result["total_cost"],
                "starting_xi_points": result["starting_xi_points"],
                "starting_xi": result["starting_xi"],
                "bench": result["bench"],
            },
            f,
            indent=2,
        )
    print(f"\nSaved -> {out_path}")

    proj_path = os.path.join(PROCESSED_DIR, f"projections_{tag}.json")
    with open(proj_path, "w") as f:
        json.dump(projections, f, indent=2)
    print(f"Saved all {len(projections)} player projections -> {proj_path}")


if __name__ == "__main__":
    main()
