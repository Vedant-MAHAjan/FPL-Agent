"""Wildcard drafts: rebuild the whole squad and compare the options before spending the chip.

Each draft is a full 15 (XI, captain, bank) from the owned squad's selling value, scored on two
horizons - the next 3 gameweeks (the decision horizon) and the next 6 (does it hold up after the
first three fixtures?) - against the control: the best plan WITHOUT a wildcard, using the normal free
transfers. Recent form (last 3 gameweeks' points and xGI) is shown beside every player but not
weighted into projections: the recent-form backtest found weighting it made predictions worse.

  A. best squad over the next 3 gameweeks
  B. best real alternative: the best 3-gameweek squad sharing at most MAX_SHARED players with A, so
     there's always a different option and its cost is visible (a 6-gameweek draft used to sit
     here, but every draft already shows a 6-gameweek score and it often came out identical to A)
  C. top-100 core: players owned by most of the current top 100, the rest optimized around them

    python src/wildcard.py 11
"""

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import opponent_strength
import recent_form
import squad_sync
from fpl_api import (get_bootstrap_static, get_classic_league_standings, get_entry_picks, get_event_live,
                     get_fixtures)
from optimizer import optimize_squad, squad_rules_from_bootstrap
from projections import build_multi_gw_projections, load_preseason_rate_baseline
from transfer import _owned_context, best_transfer_plan

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
DEFAULT_ENTRY = 2360640
OVERALL_LEAGUE = 314
MAX_SHARED = 10       # draft B may keep at most this many of draft A's 15
CORE_OWNERSHIP = 0.5   # a "top-100 core" player is owned by at least half of the top 100
FORM_GWS = 3


def score_squad(squad_ids: set, projections: list, rules: dict) -> dict:
    """Best XI + captain for a FIXED 15 under a set of projections. Scored as an owned squad making
    zero transfers, so an unavailable player (injured, loaned out) can sit on the bench."""
    pool = [p for p in projections if p["id"] in squad_ids]
    return optimize_squad(pool, rules, owned_ids=set(squad_ids), sell_price_by_id={p["id"]: 0 for p in pool},
                          bank=0, transfer_count=0)


def top100_ownership(gw: int, n: int = 100) -> dict:
    """id -> share of the overall top n who had him in their 15 for gameweek gw."""
    entries = []
    page = 1
    while len(entries) < n:
        results = get_classic_league_standings(OVERALL_LEAGUE, page)["standings"]["results"]
        if not results:
            break
        entries += [r["entry"] for r in results if r["entry"] not in entries]
        page += 1
    entries = entries[:n]
    counts = Counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for picks in pool.map(lambda e: get_entry_picks(e, gw)["picks"], entries):
            counts.update(p["element"] for p in picks)
    return {pid: c / len(entries) for pid, c in counts.items()}


def recent_form_table(last_gw: int, gws: int = FORM_GWS) -> dict:
    """id -> {'points': [...], 'xgi': total} over the last `gws` finished gameweeks."""
    table = {}
    for gw in range(last_gw - gws + 1, last_gw + 1):
        for e in get_event_live(gw)["elements"]:
            row = table.setdefault(e["id"], {"points": [], "xgi": 0.0})
            row["points"].append(e["stats"]["total_points"])
            row["xgi"] += float(e["stats"]["expected_goal_involvements"])
    return table


def _draft_summary(label, squad_ids, owned_ids, p3, p6, rules, bank_left, form, top100):
    s3, s6 = score_squad(squad_ids, p3, rules), score_squad(squad_ids, p6, rules)
    xi = {p["id"] for p in s3["starting_xi"]}
    players = []
    for p in sorted(s3["squad"], key=lambda p: (p["id"] not in xi, p["element_type"], -p["projected_points"])):
        f = form.get(p["id"], {"points": [], "xgi": 0.0})
        players.append({
            "id": p["id"], "name": p["web_name"], "pos": p["element_type"], "team": p["team"],
            "price": p["now_cost"] / 10, "starts": p["id"] in xi, "kept": p["id"] in owned_ids,
            "proj_3gw": round(p["projected_points"], 1),
            "recent_points": f["points"], "recent_xgi": round(f["xgi"], 2),
            "sustainability": p.get("sustainability"),
            "top100_own": round(top100.get(p["id"], 0.0), 2),
        })
    return {
        "label": label,
        "proj_3gw": round(s3["starting_xi_points"], 1),
        "proj_6gw": round(s6["starting_xi_points"], 1),
        "captain": s3["captain"]["web_name"], "vice_captain": s3["vice_captain"]["web_name"],
        "changes": len(squad_ids - owned_ids),
        "bank": bank_left / 10,
        "top100_overlap": sum(1 for pid in squad_ids if top100.get(pid, 0) >= CORE_OWNERSHIP),
        "players": players,
    }


def build_drafts(gw: int, entry: int = DEFAULT_ENTRY) -> dict:
    bootstrap = get_bootstrap_static()
    fixtures = get_fixtures()
    snap = os.path.join(RAW_DIR, f"bootstrap_static_{date.today().isoformat()}.json")
    with open(snap, "w") as f:
        json.dump(bootstrap, f)

    state = squad_sync.sync_squad_state(entry)
    completed = recent_form.completed_gameweeks(bootstrap)
    blended = recent_form.build_blended_baseline(bootstrap, load_preseason_rate_baseline(RAW_DIR, snap),
                                                 recent_mins=recent_form.fetch_recent_minutes(completed))
    diff = opponent_strength.make_difficulty_fn(opponent_strength.build_team_ratings(fixtures, bootstrap), completed)
    p3 = build_multi_gw_projections(bootstrap, fixtures, start_gw=gw, num_gws=3, rate_baseline=blended, difficulty_fn=diff)
    p6 = build_multi_gw_projections(bootstrap, fixtures, start_gw=gw, num_gws=6, rate_baseline=blended, difficulty_fn=diff)
    rules = squad_rules_from_bootstrap(bootstrap)

    p3, owned_ids, sell = _owned_context(p3, state)
    p6, _, _ = _owned_context(p6, state)
    budget = state["bank"] + sum(sell.values())
    form = recent_form_table(completed)
    top100 = top100_ownership(completed)

    def bank_after(squad_ids):
        cost = sum(sell[i] if i in owned_ids else next(p["now_cost"] for p in p3 if p["id"] == i) for i in squad_ids)
        return budget - cost

    def wildcard(projections, must=None, max_shared=None):
        r = optimize_squad(projections, rules, owned_ids=owned_ids, sell_price_by_id=sell, bank=state["bank"],
                           max_transfers=rules["squad_size"], must_include=must, max_shared=max_shared)
        return {p["id"] for p in r["squad"]}

    drafts = []
    a = wildcard(p3)
    drafts.append(_draft_summary("A. best over next 3 GWs", a, owned_ids, p3, p6, rules, bank_after(a), form, top100))
    b = wildcard(p3, max_shared=(a, MAX_SHARED))
    drafts.append(_draft_summary(f"B. best alternative (shares at most {MAX_SHARED} with A)", b, owned_ids, p3, p6,
                                 rules, bank_after(b), form, top100))

    # Top-100 core, most-owned first; drop the least-owned until the forced core is affordable/legal.
    core = sorted((pid for pid, share in top100.items() if share >= CORE_OWNERSHIP), key=lambda pid: -top100[pid])
    core = [pid for pid in core if any(p["id"] == pid and p["status"] == "a" for p in p3)]
    c, dropped = None, []
    while core:
        try:
            c = wildcard(p3, must=set(core))
            break
        except RuntimeError:
            dropped.append(core.pop())
    if c is None:
        c = a
    dc = _draft_summary("C. top-100 core", c, owned_ids, p3, p6, rules, bank_after(c), form, top100)
    dc["core_forced"], dc["core_dropped_to_fit"] = len(core), len(dropped)
    drafts.append(dc)

    plan = best_transfer_plan(p3, rules, state, state["free_transfers"])
    ctrl_ids = {p["id"] for p in plan["best"]["result"]["squad"]}
    control = _draft_summary(f"CONTROL: no wildcard ({plan['best']['num_transfers']} transfer(s), hit {plan['best']['hit']})",
                             ctrl_ids, owned_ids, p3, p6, rules, bank_after(ctrl_ids), form, top100)
    control["proj_3gw"] = round(control["proj_3gw"] - plan["best"]["hit"], 1)
    control["proj_6gw"] = round(control["proj_6gw"] - plan["best"]["hit"], 1)
    for d in drafts:
        d["gain_3gw_vs_control"] = round(d["proj_3gw"] - control["proj_3gw"], 1)
        d["gain_6gw_vs_control"] = round(d["proj_6gw"] - control["proj_6gw"], 1)

    out = {"built_at": datetime.now(timezone.utc).isoformat(), "gw": gw, "budget": budget / 10,
           "free_transfers": state["free_transfers"], "drafts": drafts, "control": control}
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    path = os.path.join(PROCESSED_DIR, f"wildcard_gw{gw}_{date.today().isoformat()}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    out["path"] = path
    return out


def format_draft(d: dict, teams: dict) -> str:
    pos = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    head = (f"\n{d['label']}: 3GW {d['proj_3gw']} ({d.get('gain_3gw_vs_control', 0):+}) · "
            f"6GW {d['proj_6gw']} ({d.get('gain_6gw_vs_control', 0):+}) · C {d['captain']} · VC {d['vice_captain']} · "
            f"{d['changes']} changes · bank £{d['bank']:.1f}m · top-100 core {d['top100_overlap']}/15")
    rows = []
    for p in d["players"]:
        rows.append(f"    {'XI ' if p['starts'] else 'bch'} {pos[p['pos']]:3} {p['name']:14} {teams[p['team']]:4} £{p['price']:<4} "
                    f"proj3={p['proj_3gw']:>4} last3={'/'.join(map(str, p['recent_points'])):9} xGI={p['recent_xgi']:<4} "
                    f"{(p['sustainability'] or ''):6} top100={p['top100_own']:.0%}{'  (kept)' if p['kept'] else ''}")
    return head + "\n" + "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gw", type=int)
    ap.add_argument("--entry", type=int, default=DEFAULT_ENTRY)
    args = ap.parse_args()
    out = build_drafts(args.gw, args.entry)
    teams = {t["id"]: t["short_name"] for t in get_bootstrap_static()["teams"]}
    print(f"GW{args.gw} wildcard drafts · budget £{out['budget']:.1f}m · free transfers {out['free_transfers']}")
    for d in out["drafts"] + [out["control"]]:
        print(format_draft(d, teams))
    print(f"\nsaved -> {os.path.relpath(out['path'])}")


if __name__ == "__main__":
    main()
