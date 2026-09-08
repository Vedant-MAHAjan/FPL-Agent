"""Judgment layer (component 2): a qualitative overlay on the numeric baseline.

Step 3 of the build order is explicitly a *manual* test - hand-authored judgment calls checked
against real current team news - not an automated news pipeline. Free RSS/public-feed ingestion
is later work; for now a human (or an LLM acting as one, in a single conversation) reads real
news and produces a judgment call using the structure below. The goal is to prove the layer adds
real signal the optimizer cannot see, not to restate the optimizer's own numbers as prose.

Action space (per CLAUDE.md's resolution of the open design question - confirm/veto-only by
default): CONFIRM the pick, ADJUST its confidence/framing without changing squad composition, or
VETO it outright. A VETO never invents a new pick - it falls back to the optimizer's own
next-best-ranked alternative already in the squad (bench player in the same position for a
starting-XI veto; the vice-captain for a captaincy veto). Widening this to let judgment propose
picks the optimizer never ranked is explicitly deferred, per the open decision in CLAUDE.md.
"""

CONFIRM = "CONFIRM"
ADJUST = "ADJUST"
VETO = "VETO"

HIGH = "High"
MEDIUM = "Medium"
LOW = "Low"

VALID_ACTIONS = {CONFIRM, ADJUST, VETO}
VALID_CONFIDENCE = {HIGH, MEDIUM, LOW}


def judgment_call(player_id, player_name, projected_points, action, confidence, rationale, sources=None):
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid action: {action}")
    if confidence not in VALID_CONFIDENCE:
        raise ValueError(f"invalid confidence: {confidence}")
    return {
        "player_id": player_id,
        "player_name": player_name,
        "projected_points": projected_points,  # at time of call - lets calibration check actual vs. this later
        "action": action,
        "confidence": confidence,
        "rationale": rationale,
        "sources": sources or [],
    }


def _position_name(element_type):
    return {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}[element_type]


def apply_judgment(squad_result: dict, calls: list) -> dict:
    """Overlay hand-authored judgment calls onto the optimizer's squad result.

    Every starting-XI/bench/captain slot not covered by an explicit call is left as a default
    CONFIRM at High confidence (stats-grounded, no qualitative override) - this is what makes
    "High confidence" mean something: it's the absence of a judgment override, not a label
    someone chose.
    """
    starting_xi = list(squad_result["starting_xi"])
    bench = list(squad_result["bench"])
    captain = squad_result["captain"]
    vice_captain = squad_result["vice_captain"]

    calls_by_id = {c["player_id"]: c for c in calls}
    log = []

    for call in calls:
        pid = call["player_id"]
        in_starting = any(p["id"] == pid for p in starting_xi)
        is_captain = captain["id"] == pid

        if call["action"] == VETO:
            if is_captain:
                new_captain = vice_captain
                log.append({
                    **call,
                    "replacement_id": new_captain["id"],
                    "effect": f"captaincy VETOed -> reassigned to vice-captain {new_captain['web_name']}",
                })
                captain = new_captain
                vice_captain = max(
                    (p for p in starting_xi if p["id"] != captain["id"]),
                    key=lambda p: p["projected_points"],
                )
                continue
            if in_starting:
                vetoed = next(p for p in starting_xi if p["id"] == pid)
                same_pos_bench = [p for p in bench if p["element_type"] == vetoed["element_type"]]
                if same_pos_bench:
                    replacement = max(same_pos_bench, key=lambda p: p["projected_points"])
                    starting_xi = [replacement if p["id"] == pid else p for p in starting_xi]
                    bench = [vetoed if p["id"] == replacement["id"] else p for p in bench]
                    log.append(
                        {**call, "effect": f"VETOed -> replaced in starting XI by {replacement['web_name']}"}
                    )
                else:
                    log.append(
                        {
                            **call,
                            "effect": f"VETOed but no same-position ({_position_name(vetoed['element_type'])}) "
                            "bench cover in squad - flagged for a real transfer decision, not auto-resolved",
                        }
                    )
                continue
            log.append({**call, "effect": "VETOed (was a bench pick already - no squad change)"})
            continue

        # CONFIRM / ADJUST never change who's picked, only the confidence/rationale attached.
        log.append({**call, "effect": f"{call['action']}ED, confidence={call['confidence']}"})

    default_high = lambda p: {  # noqa: E731
        "player_id": p["id"],
        "player_name": p["web_name"],
        "projected_points": p["projected_points"],
        "action": CONFIRM,
        "confidence": HIGH,
        "rationale": "no qualitative signal checked - stats-only baseline",
        "sources": [],
        "effect": "CONFIRMED (default, no manual news check performed for this player)",
    }
    covered_ids = {c["player_id"] for c in calls}
    full_log = log + [default_high(p) for p in starting_xi + bench if p["id"] not in covered_ids]

    return {
        "starting_xi": starting_xi,
        "bench": bench,
        "captain": captain,
        "vice_captain": vice_captain,
        "judgment_log": full_log,
        "calls_checked": len(calls),
        "squad_size": len(starting_xi) + len(bench),
    }
