"""Full-pool value scan: rank every nailed player by position and log a timestamped watchlist.
Codifies "scan the whole pool first" as a real pipeline step instead of eyeballing a shortlist.

Ranking is by the FORWARD-LOOKING, fixture-adjusted horizon projection (projections.py), not by
trailing form/ep_next. This is deliberate: a player in hot form heading into a brutal fixture run
must rank BELOW his raw form - the whole point is to avoid recommending "good recent points" into
tough games. Trailing form and the horizon fixture multiplier are shown side by side so that
discount is visible, not buried. (If no projections are passed, it falls back to ep_next/form.)
"""
import json
import os
from datetime import date, datetime, timezone

PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
BANDS = {1: (4.0, 6.5), 2: (4.0, 7.0), 3: (5.0, 13.0), 4: (5.5, 15.9)}  # realistic upgrade ranges


def _fixstr(team_id, fixtures, teams, gws):
    out = []
    for gw in gws:
        for f in fixtures:
            if f["event"] == gw and team_id in (f["team_h"], f["team_a"]):
                opp, home = (f["team_a"], "H") if f["team_h"] == team_id else (f["team_h"], "A")
                diff = f["team_h_difficulty"] if home == "H" else f["team_a_difficulty"]
                out.append(f"{teams[opp]}({home}){diff}")
    return " ".join(out)


def scan_pool(bootstrap, fixtures, owned_ids, start_gw, horizon=3, per_pos=8, min_minutes=120,
              projections_by_id=None):
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    gws = tuple(range(start_gw, start_gw + horizon))
    result = {}
    for pos, (lo, hi) in BANDS.items():
        cands = [e for e in bootstrap["elements"]
                 if e["element_type"] == pos and e["status"] == "a"
                 and e["minutes"] >= min_minutes and lo <= e["now_cost"] / 10 <= hi]

        def proj_pts(e):
            p = (projections_by_id or {}).get(e["id"])
            return p["projected_points"] if p else None

        # Primary sort: forward-looking fixture-adjusted projection. Only fall back to the trailing
        # ep_next/form when no projection exists for a player (keeps the "hot form, hard fixtures"
        # discount in force wherever we have real data).
        cands.sort(key=lambda e: (
            -(proj_pts(e) if proj_pts(e) is not None else -1),
            -float(e["ep_next"]), -float(e["form"]),
        ))
        rows = []
        for e in cands[:per_pos]:
            p = (projections_by_id or {}).get(e["id"])
            rows.append({
                "id": e["id"], "name": e["web_name"], "team": teams[e["team"]],
                "price": e["now_cost"] / 10, "form": float(e["form"]),
                "total_points": e["total_points"], "ep_next": float(e["ep_next"]),
                "proj_horizon": round(p["projected_points"], 1) if p else None,
                "avg_fixture_mult": p["avg_fixture_mult"] if p else None,
                "xgi_per90": p.get("xgi_per90") if p else None,
                "gi_overperformance": p.get("gi_overperformance") if p else None,
                "sustainability": p.get("sustainability") if p else None,
                "value": round(e["total_points"] / (e["now_cost"] / 10), 1),
                "owned": e["id"] in owned_ids, "own_pct": float(e["selected_by_percent"]),
                "fixtures": _fixstr(e["team"], fixtures, teams, gws),
            })
        result[POS[pos]] = rows
    return result


def log_scan(scan, start_gw):
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    path = os.path.join(PROCESSED_DIR, f"scan_gw{start_gw}_{date.today().isoformat()}.json")
    with open(path, "w") as f:
        json.dump({"scanned_at": datetime.now(timezone.utc).isoformat(),
                   "start_gw": start_gw, "scan": scan}, f, indent=2)
    return path


def format_scan(scan, top=5):
    lines = []
    for pos, rows in scan.items():
        lines.append(f"\n  {pos} (nailed, ranked by fixture-adjusted horizon projection):")
        for r in rows[:top]:
            mark = " *OWNED*" if r["owned"] else ""
            proj = f"{r['proj_horizon']:>4}" if r.get("proj_horizon") is not None else "  - "
            fm = r.get("avg_fixture_mult")
            fixmark = ""  # flag the "hot form but hard run" trap explicitly
            if fm is not None:
                fixmark = f" fixSwing={fm:>4.2f}" + ("  <hard run" if fm < 0.95 else ("  <easy run" if fm > 1.05 else ""))
            # sustainability: is the form backed by underlying, or over/under-performing xGI?
            sus = r.get("sustainability")
            susmark = ""
            if sus:
                xgi = r.get("xgi_per90"); over = r.get("gi_overperformance")
                tag = {"OVER": "!OVER", "UNDER": "~UNDER", "backed": " ok"}.get(sus, "")
                susmark = f" xGI/90={xgi:>4} {tag}(G+A{over:+.1f}vsxGI)"
            lines.append(f"    {r['name']:13s}{r['team']:5s}£{r['price']:>4.1f} form={r['form']:>4} "
                         f"proj={proj}{fixmark}{susmark} own={r['own_pct']:>4}%{mark}")
    return "\n".join(lines)
