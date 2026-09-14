"""Price-change tracking (CLAUDE.md component 3 explicitly names "team value trend" as something
memory should track, but nothing built it). Two things, both approximate by nature:

1. price_change_risk(): FPL's actual nightly price-change algorithm is proprietary/undisclosed -
   this is a same-day heuristic ranking net transfer velocity (transfers_in_event minus
   transfers_out_event, both already in bootstrap-static), not a precise predictor. Useful for
   "who's trending," not "this will definitely rise tonight."
2. compare_snapshots(): the real, precise version - actual now_cost deltas between two dated
   bootstrap-static pulls in data/raw/. Needs at least two snapshots from different days to
   produce anything; with only today's pull on record, there's nothing to diff against yet.
"""


def net_transfer_pressure(player: dict) -> int:
    return player.get("transfers_in_event", 0) - player.get("transfers_out_event", 0)


def price_change_risk(bootstrap: dict, top_n: int = 10) -> dict:
    players = bootstrap["elements"]
    ranked = sorted(players, key=net_transfer_pressure, reverse=True)
    risers = [p for p in ranked[:top_n] if net_transfer_pressure(p) > 0]
    fallers = [p for p in sorted(players, key=net_transfer_pressure)[:top_n] if net_transfer_pressure(p) < 0]
    return {
        "risers": [{"web_name": p["web_name"], "net_transfers": net_transfer_pressure(p), "now_cost": p["now_cost"]} for p in risers],
        "fallers": [{"web_name": p["web_name"], "net_transfers": net_transfer_pressure(p), "now_cost": p["now_cost"]} for p in fallers],
    }


def compare_snapshots(old_bootstrap: dict, new_bootstrap: dict) -> list:
    """Real price deltas between two dated bootstrap-static pulls - the ground truth version of
    the heuristic above, once enough daily snapshots exist in data/raw/ to diff."""
    old_by_id = {p["id"]: p["now_cost"] for p in old_bootstrap["elements"]}
    changes = []
    for p in new_bootstrap["elements"]:
        old_cost = old_by_id.get(p["id"])
        if old_cost is not None and old_cost != p["now_cost"]:
            changes.append({
                "id": p["id"], "web_name": p["web_name"],
                "old_cost": old_cost, "new_cost": p["now_cost"], "delta": p["now_cost"] - old_cost,
            })
    return sorted(changes, key=lambda c: c["delta"], reverse=True)
