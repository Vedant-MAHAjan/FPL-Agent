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

from fpl_api import get_bootstrap_static, get_entry_picks, get_event_live
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

def log_decision(gw: int, horizon: tuple, decision_packet: dict, baseline_result: dict,
                 baseline_points_hit: int = 0) -> dict:
    """Append a new (unresolved) decision record. decision_packet is expected to look like the
    output of judgment.apply_judgment() - captain/starting_xi/bench + judgment_log. baseline_result
    is the RAW optimizer output before judgment touched it (optimizer.optimize_squad's return
    value) - logged alongside so the season-level success metric CLAUDE.md actually asks for
    ("cumulative points/rank vs. a numeric-only baseline") is computable once gameweeks resolve,
    instead of being lost the moment judgment overlays the final call.

    Both bench orders and the baseline plan's points hit are stored too, so record_outcome can
    score each side the way FPL actually would: auto-subs from that side's own bench, minus the
    hit that side's transfers cost. Without them a baseline that takes a -4 looks 4 points better
    than it really was.
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
        "bench_ids": [p["id"] for p in decision_packet.get("bench", [])],
        "baseline_bench_ids": [p["id"] for p in baseline_result.get("bench", [])],
        "baseline_vice_captain_id": (baseline_result.get("vice_captain") or {}).get("id"),
        "baseline_points_hit": baseline_points_hit,
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


# FPL's minimum outfield formation (overridden from bootstrap element_types when available).
DEFAULT_MIN_PLAY = {1: 1, 2: 3, 3: 2, 4: 1}


def apply_auto_subs(xi_ids: list, bench_ids: list, minutes_by_id: dict, element_type_by_id: dict,
                    min_play: dict = None) -> tuple:
    """FPL's automatic substitution, applied to one side of the comparison. Each starter who played
    0 minutes is replaced, in XI order, by the first bench player (in bench order) who played and
    whose swap keeps a legal formation; a goalkeeper can only be replaced by a goalkeeper.
    Returns (final_xi_ids, [(out_id, in_id), ...]).
    """
    min_play = min_play or DEFAULT_MIN_PLAY
    xi = list(xi_ids)
    used = set()
    subs = []
    for out_id in list(xi):
        if minutes_by_id.get(out_id, 0) > 0:
            continue
        out_pos = element_type_by_id[out_id]
        for in_id in bench_ids:
            if in_id in used or in_id in xi or minutes_by_id.get(in_id, 0) <= 0:
                continue
            in_pos = element_type_by_id[in_id]
            if (out_pos == 1) != (in_pos == 1):
                continue
            after = [in_id if p == out_id else p for p in xi]
            counts = {pos: 0 for pos in min_play}
            for p in after:
                counts[element_type_by_id[p]] += 1
            if all(counts[pos] >= n for pos, n in min_play.items()):
                xi = after
                used.add(in_id)
                subs.append((out_id, in_id))
                break
    return xi, subs


def _score_side(xi_ids, bench_ids, captain_id, vice_id, points_hit, pts, minutes_by_id,
                element_type_by_id, min_play):
    """Points one side actually banks: XI after auto-subs, captain doubled (armband passes to the
    vice-captain if the captain didn't play), minus the side's transfer hit. Auto-subs are skipped
    when minutes or the bench aren't known, and that is reported rather than guessed."""
    subs = []
    autosubs_applied = minutes_by_id is not None and bool(bench_ids)
    if autosubs_applied:
        xi_ids, subs = apply_auto_subs(xi_ids, bench_ids, minutes_by_id, element_type_by_id, min_play)
    armband = captain_id
    if minutes_by_id is not None and minutes_by_id.get(captain_id, 0) == 0 and vice_id is not None \
            and minutes_by_id.get(vice_id, 0) > 0:
        armband = vice_id
    gross = sum(pts(p) for p in xi_ids) + pts(armband)
    return {"gross": gross, "hit": points_hit, "total": gross - points_hit, "armband_id": armband,
            "auto_subs": subs, "autosubs_applied": autosubs_applied}


def _actual_lineup(gw: int) -> dict | None:
    """What the manager actually fielded in gw (the recommendation can be ignored or changed in
    the app). None if the picks can't be fetched."""
    try:
        data = get_entry_picks(get_squad_state()["entry_id"], gw)
    except Exception:
        return None
    picks = sorted(data["picks"], key=lambda p: p["position"])
    hist = data.get("entry_history", {})
    return {
        "xi_ids": [p["element"] for p in picks if p["position"] <= 11],
        "bench_ids": [p["element"] for p in picks if p["position"] > 11],
        "captain_id": next((p["element"] for p in picks if p["is_captain"]), None),
        "vice_captain_id": next((p["element"] for p in picks if p["is_vice_captain"]), None),
        "chip": data.get("active_chip"),
        "points": hist.get("points"),
        "points_hit": hist.get("event_transfers_cost"),
    }


def _actual_squad(gw: int) -> list:
    """The manager's real 15 for gw in pick order (bench last, in bench order), used when a record
    predates logged bench orders. Empty if the picks can't be fetched."""
    try:
        state = get_squad_state()
        picks = get_entry_picks(state["entry_id"], gw)["picks"]
    except Exception:
        return []
    return [p["element"] for p in sorted(picks, key=lambda p: p["position"])]


def record_outcome(gw: int, live_points_by_id: dict = None, force: bool = False,
                   live_minutes_by_id: dict = None, element_type_by_id: dict = None,
                   rescore: bool = False) -> dict:
    """Fill in the actual outcome for a previously logged gw. Pulls actual per-player gameweek
    points from the live FPL endpoint unless a live_points_by_id map is passed in directly
    (used for testing, since no gameweek has actually finished yet this season) - passing
    live_points_by_id explicitly always skips the finalization gate below, same as force=True.

    Computes three things CLAUDE.md's success metric explicitly calls for and the original
    version of this function didn't: the numeric-only baseline's actual result (for the
    season-level judgment-vs-baseline comparison), the captain-vs-vice-captain "road not taken",
    and - for any VETO call - the vetoed player's actual points vs. their replacement's, so a
    veto's correctness is checkable, not just asserted.

    Each side is scored as FPL would score it: auto-subs from its own bench, armband to the vice
    if the captain didn't play, and minus its own transfer hit. rescore=True re-scores an already
    resolved record (used to backfill after this scoring changed).
    """
    records = _load_all_decisions()
    matches = [r for r in records if r["gw"] == gw and (rescore or not r["resolved"])]
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
        live_minutes_by_id = {e["id"]: e["stats"]["minutes"] for e in live["elements"]}
        actual = _actual_lineup(gw)
        if actual:
            record["actual"] = actual

    min_play = None
    if live_minutes_by_id is not None and element_type_by_id is None:
        bootstrap = get_bootstrap_static()
        element_type_by_id = {e["id"]: e["element_type"] for e in bootstrap["elements"]}
        min_play = {et["id"]: et["squad_min_play"] for et in bootstrap["element_types"]}

    def pts(pid):
        return live_points_by_id.get(pid, 0)

    # Records logged before bench orders were stored fall back to the manager's real 15 minus that
    # side's XI (the real lineup can differ from the logged one). The baseline never bought the
    # judgment side's transfer-ins, so those can't sit on its bench.
    actual_squad = None

    def bench_for(logged, xi, exclude=()):
        nonlocal actual_squad
        if logged:
            return logged, "logged"
        if live_minutes_by_id is None:
            return [], "none"
        if actual_squad is None:
            actual_squad = _actual_squad(gw)
        return [p for p in actual_squad if p not in xi and p not in exclude], "actual_squad_fallback"

    transfer_in = (record.get("transfer") or {}).get("in") or []
    judgment_ins = {p["id"] for p in (transfer_in if isinstance(transfer_in, list) else [transfer_in])}
    j_bench, j_src = bench_for(record.get("bench_ids") or record.get("bench_order_ids"),
                               record["starting_xi_ids"])
    b_bench, b_src = bench_for(record.get("baseline_bench_ids"), record["baseline_starting_xi_ids"],
                               exclude=judgment_ins)
    judgment_hit = (record.get("transfer") or {}).get("points_hit", 0) or 0
    baseline_hit = record.get("baseline_points_hit", 0) or 0

    j = _score_side(record["starting_xi_ids"], j_bench, record["captain_id"],
                    record["vice_captain_id"], judgment_hit, pts, live_minutes_by_id,
                    element_type_by_id, min_play)
    b = _score_side(record["baseline_starting_xi_ids"], b_bench, record["baseline_captain_id"],
                    record.get("baseline_vice_captain_id"), baseline_hit, pts, live_minutes_by_id,
                    element_type_by_id, min_play)
    captain_pts = pts(record["captain_id"])
    baseline_captain_pts = pts(record["baseline_captain_id"])
    total, baseline_total = j["total"], b["total"]

    vice_pts = pts(record["vice_captain_id"])
    captain_choice_delta = captain_pts - vice_pts  # >0 = captaining this player beat captaining the VC

    # projected_points is a HORIZON total (summed over every GW in record["horizon"], see
    # projections.project_player_horizon), but `actual` below is a SINGLE gameweek's points.
    # Comparing the two directly is a unit mismatch - it made multi-GW projections look like
    # chronic misses and skewed the "High confidence underperforms" calibration finding. Normalize
    # the projection to a per-GW figure so both sides of beat_projection are in the same unit.
    # horizon is stored as an inclusive (start_gw, end_gw) pair (see judgment_pass.log_decision),
    # so the number of gameweeks it spans is end - start + 1 - NOT len(horizon), which is always 2.
    horizon = record.get("horizon") or []
    n_gws = (horizon[1] - horizon[0] + 1) if len(horizon) == 2 else max(len(horizon), 1)

    call_hits = []
    for call in record["judgment_log"]:
        pid = call["player_id"]
        if pid not in live_points_by_id:
            continue
        actual = live_points_by_id[pid]
        projected = call.get("projected_points")
        projected_per_gw = (projected / n_gws) if projected is not None else None
        outcome = {
            "player_id": pid, "confidence": call["confidence"], "action": call["action"],
            "projected_points": projected,  # horizon total, as logged
            "projected_points_per_gw": (round(projected_per_gw, 2) if projected_per_gw is not None else None),
            "horizon_gws": n_gws,
            "actual_points": actual,
            "beat_projection": (actual >= projected_per_gw) if projected_per_gw is not None else None,
            # Explicit flag wins: a cited opinion piece is a source but not hard news.
            "news_backed": call.get("news_backed", bool(call.get("sources")) and not call.get("unsupported_override")),
            "unsupported_override": call.get("unsupported_override", False),
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
        "judgment_scoring": {**j, "bench_source": j_src},
        "baseline_scoring": {**b, "bench_source": b_src},
        "captain_choice_delta": captain_choice_delta,
        # What was really played: FPL's own points (after hits), and whether the real lineup
        # followed the recommendation - the scoreboard above grades the recommendation itself.
        "actual_points": (record.get("actual") or {}).get("points"),
        "lineup_followed": (
            set(record["actual"]["xi_ids"]) == set(record["starting_xi_ids"])
            and record["actual"]["captain_id"] == record["captain_id"]
        ) if record.get("actual") else None,
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
    # Overrides only (anything but CONFIRM), split by whether a news source backed them - the
    # question after GW6 is whether argument-only overrides ever beat the model.
    overrides = {"news_backed": [], "argument_only": []}
    for r in records:
        for o in r["outcome"]["judgment_call_outcomes"]:
            if o["action"] != "CONFIRM" and o["beat_projection"] is not None:
                # A VETO is right when the vetoed player does NOT reach his projection.
                right = (not o["beat_projection"]) if o["action"] == VETO else o["beat_projection"]
                overrides["news_backed" if o.get("news_backed") else "argument_only"].append(right)
    by_support = {k: {"n": len(v), "right_rate": round(sum(v) / len(v), 3) if v else None}
                  for k, v in overrides.items()}
    return {"status": f"{len(records)} resolved gameweek(s)", "by_confidence": summary,
            "overrides_by_support": by_support}


def compute_season_comparison() -> dict:
    """Cumulative judgment-adjusted points vs. the numeric-only baseline across every resolved
    gameweek - CLAUDE.md's actual season-level success metric ("cumulative points/rank vs. a
    numeric-only baseline... this is the real test of whether the judgment layer earns its
    complexity"). Needs log_decision() to have been called with a baseline_result each week.

    Weeks with no agent decision (no_agent_decision: the manager played without a discussion) are
    kept out of the judgment metric - there was no judgment to grade - and reported separately as
    manager-vs-model.
    """
    resolved = [r for r in _load_all_decisions() if r["resolved"]]
    records = [r for r in resolved if not r.get("no_agent_decision")]
    unguided = [r for r in resolved if r.get("no_agent_decision")]
    if not records:
        return {"status": "no resolved gameweeks yet"}

    judgment_total = sum(r["outcome"]["starting_xi_total"] for r in records)
    baseline_total = sum(r["outcome"]["baseline_starting_xi_total"] for r in records)
    return {
        "status": f"{len(records)} resolved gameweek(s)",
        "no_discussion_weeks": {r["gw"]: r["outcome"]["judgment_delta"] for r in unguided},
        "judgment_total": judgment_total,
        "baseline_total": baseline_total,
        "delta": judgment_total - baseline_total,
        "judgment_wins": sum(1 for r in records if r["outcome"]["judgment_delta"] > 0),
        "baseline_wins": sum(1 for r in records if r["outcome"]["judgment_delta"] < 0),
        "ties": sum(1 for r in records if r["outcome"]["judgment_delta"] == 0),
    }
