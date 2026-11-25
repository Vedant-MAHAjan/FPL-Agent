"""Ghost control: what would copying a top-10% manager have scored?

One copied manager mostly measures that manager's luck, so this tracks a PANEL: random managers
(sampled by random entry id, so selection says nothing about future luck) who were in the overall
top 10% at the end of a chosen gameweek. From the next gameweek on, the panel's average score (net of
transfer hits) is recorded beside our own score and the model's, so "copy the top players or follow
the model?" gets answered by real results.

    python src/ghost.py build 11      # pick the panel as of GW11 (one-off, ~6,000 API calls)
    python src/ghost.py score 12      # panel average for GW12 (run_gw does this on resolve)
"""

import argparse
import json
import os
import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fpl_api import get_bootstrap_static, get_entry_history

PANEL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "memory", "ghost_panel.json")
TOP_SHARE = 0.10
MAX_PANEL = 100


def _history(entry_id):
    try:
        return entry_id, get_entry_history(entry_id)["current"]
    except Exception:
        return entry_id, None


def select_panel(histories: dict, as_of_gw: int, cutoff_rank: int, max_size: int = MAX_PANEL) -> list:
    """Entry ids that played every gameweek up to as_of_gw and were inside cutoff_rank after it."""
    chosen = []
    for eid, rows in histories.items():
        by_gw = {r["event"]: r for r in rows or []}
        if all(g in by_gw for g in range(1, as_of_gw + 1)) and by_gw[as_of_gw]["overall_rank"] <= cutoff_rank:
            chosen.append(eid)
    return sorted(chosen)[:max_size]


def panel_mean(histories: dict, gw: int):
    """Mean points net of hits for gameweek gw across the panel (managers missing that gw skipped)."""
    pts = [r["points"] - r["event_transfers_cost"] for rows in histories.values() for r in rows or [] if r["event"] == gw]
    return (round(statistics.mean(pts), 1), len(pts)) if pts else (None, 0)


def build(as_of_gw: int, sample: int = 6000, seed: int = 11) -> dict:
    total = get_bootstrap_static()["total_players"]
    rng = random.Random(seed)
    ids = rng.sample(range(1, total + 1), sample)
    with ThreadPoolExecutor(16) as pool:
        histories = dict(pool.map(_history, ids))
    panel = select_panel(histories, as_of_gw, int(total * TOP_SHARE))
    out = {"built_at": datetime.now(timezone.utc).isoformat(), "as_of_gw": as_of_gw, "top_share": TOP_SHARE,
           "sampled": sample, "total_players": total, "entries": panel}
    with open(PANEL_PATH, "w") as f:
        json.dump(out, f, indent=2)
    return out


def score(gw: int):
    """(panel mean, n) for gw, or (None, 0) when there's no panel or the gw predates it."""
    if not os.path.exists(PANEL_PATH):
        return None, 0
    with open(PANEL_PATH) as f:
        panel = json.load(f)
    if gw <= panel["as_of_gw"]:
        return None, 0
    with ThreadPoolExecutor(16) as pool:
        histories = dict(pool.map(_history, panel["entries"]))
    return panel_mean(histories, gw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["build", "score"])
    ap.add_argument("gw", type=int)
    args = ap.parse_args()
    if args.action == "build":
        out = build(args.gw)
        print(f"panel: {len(out['entries'])} top-{TOP_SHARE:.0%} managers as of GW{args.gw} "
              f"(from {out['sampled']} random entries) -> {os.path.relpath(PANEL_PATH)}")
    else:
        mean, n = score(args.gw)
        print(f"GW{args.gw}: panel average {mean} over {n} managers")


if __name__ == "__main__":
    main()
