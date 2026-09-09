"""Persistent memory (component 3): file-based log of every recommendation + its eventual
outcome, plus a running squad-state snapshot (chips, transfers, value) so each week's call is
grounded in actual season trajectory rather than being stateless.

Two files, both under data/memory/:
  - decisions.jsonl: one JSON record per gameweek's decision packet, append-only. record_outcome()
    fills in the actual result once a gameweek finishes.
  - squad_state.json: the current squad + bank + free transfers + chip inventory. Updated
    in place as the season progresses (transfers made, chips played).

No gameweek has resolved yet this season (preseason) - decisions.jsonl will genuinely be empty
of outcomes until step 4 starts running live from GW1. compute_calibration() is expected to
report "no resolved data yet" until then; that's correct behavior, not a bug.
"""

import json
import os
from datetime import datetime, timezone

from fpl_api import get_bootstrap_static, get_event_live
from judgment import VETO

MEMORY_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "memory")
DECISIONS_PATH = os.path.join(MEMORY_DIR, "decisions.jsonl")
CHIP_DECISIONS_PATH = os.path.join(MEMORY_DIR, "chip_decisions.jsonl")
SQUAD_STATE_PATH = os.path.join(MEMORY_DIR, "squad_state.json")


# --- squad state -------------------------------------------------------------------------

def chip_windows_from_bootstrap(bootstrap: dict) -> dict:
    """chip_key (e.g. 'wildcard_1') -> {name, start_event, stop_event}, derived live from
    bootstrap's chips list rather than hardcoded, so it stays correct if FPL changes chip rules.
    """
    windows = {}
    counts = {}
    for c in bootstrap["chips"]:
        counts[c["name"]] = counts.get(c["name"], 0) + 1
        key = f"{c['name']}_{counts[c['name']]}"
        windows[key] = {"name": c["name"], "start_event": c["start_event"], "stop_event": c["stop_event"]}
    return windows


def init_squad_state(bootstrap: dict, squad_result: dict, as_of_gw: int, free_transfers: int = 1, entry_id: int = None) -> dict:
    """Create the initial squad-state file from an optimizer result. Refuses to overwrite an
    existing state file - use record_* functions to evolve state once it exists, so a season's
    real history is never silently clobbered by re-running the initial squad selection.
    """
    if os.path.exists(SQUAD_STATE_PATH):
        raise FileExistsError(
            f"{SQUAD_STATE_PATH} already exists - refusing to overwrite season state. "
            "Delete it explicitly first if you really mean to reset the season."
        )

    squad = squad_result["starting_xi"] + squad_result["bench"]
    squad_value = sum(p["now_cost"] for p in squad)
    budget = bootstrap["game_settings"]["squad_total_spend"]

    state = {
        "as_of_gw": as_of_gw,
        "entry_id": entry_id,  # real FPL entry, once known - lets record_outcome() eventually
        # cross-check this against your actual picks via entry/{id}/event/{gw}/picks/, once that
        # becomes visible after the deadline passes (it's not public pre-deadline, even for you).
        "squad": [
            {"id": p["id"], "web_name": p["web_name"], "team": p["team"], "element_type": p["element_type"],
             "purchase_price": p["now_cost"]}
            for p in squad
        ],
        "bank": budget - squad_value,
        "team_value": squad_value,
        "free_transfers": free_transfers,
        "max_banked_free_transfers": bootstrap["game_settings"]["max_extra_free_transfers"],
        "chips": {key: {**w, "available": True, "used_gw": None} for key, w in chip_windows_from_bootstrap(bootstrap).items()},
        "history": [{"gw": as_of_gw, "event": "squad_initialized", "note": "initial squad from numeric optimizer",
                     "timestamp": datetime.now(timezone.utc).isoformat()}],
    }
    _save_squad_state(state)
    return state


def get_squad_state() -> dict:
    with open(SQUAD_STATE_PATH) as f:
        return json.load(f)


def _save_squad_state(state: dict):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(SQUAD_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def record_chip_used(chip_key: str, gw: int, note: str = ""):
    state = get_squad_state()
    if chip_key not in state["chips"]:
        raise KeyError(f"unknown chip: {chip_key}")
    if not state["chips"][chip_key]["available"]:
        raise ValueError(f"{chip_key} already used in GW{state['chips'][chip_key]['used_gw']}")
    state["chips"][chip_key]["available"] = False
    state["chips"][chip_key]["used_gw"] = gw
    state["history"].append({"gw": gw, "event": f"chip_used:{chip_key}", "note": note,
                              "timestamp": datetime.now(timezone.utc).isoformat()})
    _save_squad_state(state)
    return state


def remaining_chips(state: dict, as_of_gw: int) -> list:
    """Chips still available AND still inside their usable event window."""
    return [
        key for key, c in state["chips"].items()
        if c["available"] and c["start_event"] <= as_of_gw <= c["stop_event"]
    ]


# --- chip planning log (runs every 3-4 gws, separate cadence from weekly decisions) --------

def log_chip_decision(as_of_gw: int, calls: list) -> dict:
    record = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "as_of_gw": as_of_gw,
        "calls": calls,
    }
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(CHIP_DECISIONS_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


# --- decisions log -------------------------------------------------------------------------

def log_decision(gw: int, horizon: tuple, decision_packet: dict, baseline_result: dict) -> dict:
    """Append a new (unresolved) decision record. decision_packet is expected to look like the
    output of judgment.apply_judgment() - captain/starting_xi/bench + judgment_log. baseline_result
    is the RAW optimizer output before judgment touched it (optimizer.optimize_squad's return
    value) - logged alongside so the season-level success metric CLAUDE.md actually asks for
    ("cumulative points/rank vs. a numeric-only baseline") is computable once gameweeks resolve,
    instead of being lost the moment judgment overlays the final call.
    """
    record = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "gw": gw,
        "horizon": list(horizon),
        "captain_id": decision_packet["captain"]["id"],
        "captain_name": decision_packet["captain"]["web_name"],
        "vice_captain_id": decision_packet["vice_captain"]["id"],
        "starting_xi_ids": [p["id"] for p in decision_packet["starting_xi"]],
        "baseline_captain_id": baseline_result["captain"]["id"],
        "baseline_captain_name": baseline_result["captain"]["web_name"],
        "baseline_starting_xi_ids": [p["id"] for p in baseline_result["starting_xi"]],
        "judgment_log": decision_packet.get("judgment_log", []),
        "resolved": False,
        "outcome": None,
    }
    if decision_packet.get("transfer"):
        record["transfer"] = decision_packet["transfer"]
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(DECISIONS_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def _load_all_decisions() -> list:
    if not os.path.exists(DECISIONS_PATH):
        return []
    with open(DECISIONS_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def _gameweek_finalized(gw: int) -> bool:
    """FPL's gameweek 'lockdown' - when bonus points stop being provisional - happens ~09:00 UK
    the morning after the last match of the gameweek, per the 2026/27 rule changes. events[gw]
    .data_checked flips True once that's happened. Calling record_outcome() before that risks
    locking in bonus points that still change.
    """
    bootstrap = get_bootstrap_static()
    event = next((e for e in bootstrap["events"] if e["id"] == gw), None)
    return bool(event and event["data_checked"])


def record_outcome(gw: int, live_points_by_id: dict = None, force: bool = False) -> dict:
    """Fill in the actual outcome for a previously logged gw. Pulls actual per-player gameweek
    points from the live FPL endpoint unless a live_points_by_id map is passed in directly
    (used for testing, since no gameweek has actually finished yet this season) - passing
    live_points_by_id explicitly always skips the finalization gate below, same as force=True.

    Computes three things CLAUDE.md's success metric explicitly calls for and the original
    version of this function didn't: the numeric-only baseline's actual result (for the
    season-level judgment-vs-baseline comparison), the captain-vs-vice-captain "road not taken",
    and - for any VETO call - the vetoed player's actual points vs. their replacement's, so a
    veto's correctness is checkable, not just asserted.
    """
    records = _load_all_decisions()
    matches = [r for r in records if r["gw"] == gw and not r["resolved"]]
    if not matches:
        raise KeyError(f"no unresolved logged decision found for GW{gw}")
    record = matches[-1]

    if live_points_by_id is None:
        if not force and not _gameweek_finalized(gw):
            raise RuntimeError(
                f"GW{gw} isn't finalized yet (events[{gw}].data_checked is False) - bonus points "
                "are still provisional until FPL's lockdown. Wait, or pass force=True to record "
                "provisional points anyway."
            )
        live = get_event_live(gw)
        live_points_by_id = {e["id"]: e["stats"]["total_points"] for e in live["elements"]}

    def pts(pid):
        return live_points_by_id.get(pid, 0)

    captain_pts = pts(record["captain_id"])
    starting_xi_pts = sum(pts(pid) for pid in record["starting_xi_ids"])
    total = starting_xi_pts + captain_pts  # captain's points counted twice

    baseline_captain_pts = pts(record["baseline_captain_id"])
    baseline_starting_xi_pts = sum(pts(pid) for pid in record["baseline_starting_xi_ids"])
    baseline_total = baseline_starting_xi_pts + baseline_captain_pts

    vice_pts = pts(record["vice_captain_id"])
    captain_choice_delta = captain_pts - vice_pts  # >0 = captaining this player beat captaining the VC

    call_hits = []
    for call in record["judgment_log"]:
        pid = call["player_id"]
        if pid not in live_points_by_id:
            continue
        actual = live_points_by_id[pid]
        projected = call.get("projected_points")
        outcome = {
            "player_id": pid, "confidence": call["confidence"], "action": call["action"],
            "projected_points": projected, "actual_points": actual,
            "beat_projection": (actual >= projected) if projected is not None else None,
        }
        if call["action"] == VETO and "replacement_id" in call:
            outcome["road_not_taken"] = {
                "replacement_id": call["replacement_id"],
                "replacement_actual_points": pts(call["replacement_id"]),
                "veto_delta": pts(call["replacement_id"]) - actual,  # >0 = the veto was correct
            }
        call_hits.append(outcome)

    record["resolved"] = True
    record["outcome"] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "captain_points": captain_pts,
        "starting_xi_total": total,
        "baseline_captain_points": baseline_captain_pts,
        "baseline_starting_xi_total": baseline_total,
        "judgment_delta": total - baseline_total,  # >0 = judgment layer beat the numeric-only baseline this week
        "captain_choice_delta": captain_choice_delta,
        "judgment_call_outcomes": call_hits,
    }

    # `record` is the same object as the matching entry in `records` (list comprehension keeps
    # references), so it's already updated in place - just persist the full list back to disk.
    with open(DECISIONS_PATH, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return record


def compute_calibration() -> dict:
    """Hit-rate per confidence tier across every resolved judgment call so far: did the picked
    player's actual points meet/beat their projection AT THE TIME OF THE CALL? This is the
    "Brier-score-style, checkable, not vibes" tracking CLAUDE.md calls for - a proxy for now
    (simple beat-projection hit-rate), since a literal Brier score needs an explicit predicted
    probability per call, which the judgment layer doesn't emit yet.

    Deliberately NOT just average actual points per tier: that conflates player quality with call
    correctness (a Medium-confidence Haaland call will always look great by raw points regardless
    of whether "Medium" was actually justified). beat_projection normalizes for that.
    """
    records = [r for r in _load_all_decisions() if r["resolved"]]
    if not records:
        return {"status": "no resolved gameweeks yet", "by_confidence": {}}

    by_conf = {"High": [], "Medium": [], "Low": []}
    for r in records:
        for outcome in r["outcome"]["judgment_call_outcomes"]:
            by_conf.setdefault(outcome["confidence"], []).append(outcome)

    summary = {}
    for conf, calls in by_conf.items():
        if not calls:
            summary[conf] = {"n": 0}
            continue
        scored = [c for c in calls if c["beat_projection"] is not None]
        avg_points = sum(c["actual_points"] for c in calls) / len(calls)
        entry = {"n": len(calls), "avg_actual_points": round(avg_points, 2)}
        if scored:
            entry["beat_projection_rate"] = round(sum(c["beat_projection"] for c in scored) / len(scored), 3)
        summary[conf] = entry
    return {"status": f"{len(records)} resolved gameweek(s)", "by_confidence": summary}


def compute_season_comparison() -> dict:
    """Cumulative judgment-adjusted points vs. the numeric-only baseline across every resolved
    gameweek - CLAUDE.md's actual season-level success metric ("cumulative points/rank vs. a
    numeric-only baseline... this is the real test of whether the judgment layer earns its
    complexity"). Needs log_decision() to have been called with a baseline_result each week.
    """
    records = [r for r in _load_all_decisions() if r["resolved"]]
    if not records:
        return {"status": "no resolved gameweeks yet"}

    judgment_total = sum(r["outcome"]["starting_xi_total"] for r in records)
    baseline_total = sum(r["outcome"]["baseline_starting_xi_total"] for r in records)
    return {
        "status": f"{len(records)} resolved gameweek(s)",
        "judgment_total": judgment_total,
        "baseline_total": baseline_total,
        "delta": judgment_total - baseline_total,
        "judgment_wins": sum(1 for r in records if r["outcome"]["judgment_delta"] > 0),
        "baseline_wins": sum(1 for r in records if r["outcome"]["judgment_delta"] < 0),
        "ties": sum(1 for r in records if r["outcome"]["judgment_delta"] == 0),
    }
