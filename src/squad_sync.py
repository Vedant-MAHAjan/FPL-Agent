"""Sync data/memory/squad_state.json from the live FPL API for one entry.

The weekly pipeline reasons about the *real* current squad, so this pulls the latest set team,
bank, chip usage, and a best-effort free-transfer count straight from the public API - the state
file is never hand-maintained (and never silently stale). purchase_price is NOT public, so it is
carried over from the existing state file where a player is still owned, and approximated by
now_cost for newly added players (fine early season; the sell-price nuance only matters once a
player has risen a lot).
"""
import argparse

import memory
from fpl_api import get_bootstrap_static, get_entry_history, get_entry_picks


def _latest_picks(entry_id: int, bootstrap: dict):
    """The most recent gameweek the entry has a team set for (current or next), walking back
    until a picks payload actually exists."""
    events = bootstrap["events"]
    cur = next((e["id"] for e in events if e.get("is_current")), None)
    nxt = next((e["id"] for e in events if e.get("is_next")), None)
    start = max([g for g in (cur, nxt) if g] or [1])
    for gw in range(start, 0, -1):
        try:
            picks = get_entry_picks(entry_id, gw)
        except Exception:
            continue
        if picks.get("picks"):
            return gw, picks
    raise RuntimeError(f"no set picks found for entry {entry_id}")


def _free_transfers(history: dict, bootstrap: dict) -> int:
    """Best-effort free transfers for the next gameweek after the latest completed one, derived
    from transfer history (the public API doesn't expose FT directly). Verify against the app.

    Chip-aware: on a wildcard or free-hit week transfers are unlimited and free, so they do NOT
    consume the free-transfer bank - it simply carries over and still accrues +1. The previous
    version subtracted those transfers, which wrongly zeroed the FT count after any chip week.
    """
    cap = bootstrap["game_settings"]["max_extra_free_transfers"] + 1
    free_chip_gws = {
        c["event"] for c in history.get("chips", []) if c.get("name") in ("wildcard", "freehit")
    }
    ft = 1  # available for the first transfer-eligible gw
    for row in sorted(history.get("current", []), key=lambda r: r["event"]):
        if row["event"] == 1:
            continue  # GW1 is the free, unlimited initial build
        if row["event"] in free_chip_gws:
            ft = min(cap, ft + 1)  # chip week: no FT spent, just accrue
        else:
            ft = min(cap, max(0, ft - row["event_transfers"]) + 1)
    return ft


def _chip_inventory(bootstrap: dict, history: dict) -> dict:
    windows = memory.chip_windows_from_bootstrap(bootstrap)
    chips = {k: {**w, "available": True, "used_gw": None} for k, w in windows.items()}
    for used in history.get("chips", []):
        for k, c in chips.items():
            if c["name"] == used["name"] and c["available"] and c["start_event"] <= used["event"] <= c["stop_event"]:
                c["available"] = False
                c["used_gw"] = used["event"]
                break
    return chips


def sync_squad_state(entry_id: int) -> dict:
    bootstrap = get_bootstrap_static()
    el = {e["id"]: e for e in bootstrap["elements"]}
    history = get_entry_history(entry_id)
    gw, picks = _latest_picks(entry_id, bootstrap)
    eh = picks["entry_history"]

    prior_pp = {}
    try:
        prior_pp = {p["id"]: p["purchase_price"] for p in memory.get_squad_state()["squad"]}
    except FileNotFoundError:
        pass

    squad = []
    for p in picks["picks"]:
        e = el[p["element"]]
        squad.append({
            "id": e["id"], "web_name": e["web_name"], "team": e["team"],
            "element_type": e["element_type"],
            "purchase_price": prior_pp.get(e["id"], e["now_cost"]),
        })

    cap = next((p["element"] for p in picks["picks"] if p["is_captain"]), None)
    vice = next((p["element"] for p in picks["picks"] if p["is_vice_captain"]), None)
    ft = _free_transfers(history, bootstrap)

    state = {
        "as_of_gw": gw,
        "entry_id": entry_id,
        "squad": squad,
        "bank": eh["bank"],
        "team_value": eh["value"],
        "free_transfers": ft,
        "max_banked_free_transfers": bootstrap["game_settings"]["max_extra_free_transfers"],
        "captain_id": cap,
        "vice_captain_id": vice,
        "chips": _chip_inventory(bootstrap, history),
        "synced_from": f"entry/{entry_id}/event/{gw}/picks",
    }
    memory._save_squad_state(state)
    return state


def main():
    ap = argparse.ArgumentParser(description="Sync squad_state.json from the live FPL API.")
    ap.add_argument("entry_id", type=int)
    args = ap.parse_args()
    s = sync_squad_state(args.entry_id)
    el = {e["id"]: e for e in get_bootstrap_static()["elements"]}
    print(f"Synced entry {s['entry_id']} as of GW{s['as_of_gw']} ({s['synced_from']})")
    print(f"  bank £{s['bank']/10:.1f}m · value £{s['team_value']/10:.1f}m · "
          f"free transfers {s['free_transfers']} (best-effort - verify)")
    print(f"  squad: {', '.join(el[p['id']]['web_name'] for p in s['squad'])}")
    used = [(k, c['used_gw']) for k, c in s['chips'].items() if not c['available']]
    print(f"  chips used: {used or 'none'}")


if __name__ == "__main__":
    main()
