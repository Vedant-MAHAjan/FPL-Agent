"""MILP squad optimizer (PuLP): pick a 15-man squad + starting XI + captain that maximizes
projected points, subject to the actual FPL rules pulled from bootstrap-static (budget, squad
composition, per-team limit, starting-XI formation bounds).

Two modes, same underlying MILP:
  - fresh build (owned_ids=None): unconstrained by any existing squad - used for the one-time
    initial squad pick (steps 2-3).
  - transfer mode (owned_ids given): constrained to the players already owned, funded by bank +
    sell revenue (not now_cost) for anyone transferred out, optionally capped at an exact number
    of transfers - used for the weekly transfer decision (step 4). See transfer.py for the sweep
    over transfer counts that decides whether a -4 hit is worth taking.
"""

import pulp

BENCH_WEIGHT = 0.1  # bench players count for a fraction of their projected points in the objective
UNAVAILABLE_STATUSES = ("i", "s", "u", "n")
ATTACKING_TYPES = (3, 4)  # MID, FWD element_type ids
STACK_PENALTY = 2.5  # points-equivalent penalty per starting MID/FWD beyond the first from the
# same club, over the projection horizon. Clean-sheet returns are shared across a team's
# defenders with no extra downside, so defender stacking is left alone - but goal involvements
# are ~zero-sum within a match, so two starting attackers from the same club are betting on the
# *same* goal events landing on these two specific players. The base objective is a flat sum of
# independent expected points with no covariance term, so it can't see that risk on its own; this
# is a heuristic proxy for it, not a measured correlation (no per-player joint-outcome data
# exists pre-season to estimate real covariance from).


def squad_rules_from_bootstrap(bootstrap: dict) -> dict:
    gs = bootstrap["game_settings"]
    positions = {}
    for et in bootstrap["element_types"]:
        positions[et["id"]] = {
            "squad_select": et["squad_select"],
            "squad_min_play": et["squad_min_play"],
            "squad_max_play": et["squad_max_play"],
        }
    return {
        "squad_size": gs["squad_squadsize"],
        "starting_size": gs["squad_squadplay"],
        "budget": gs["squad_total_spend"],  # tenths of a million, matches now_cost units
        "team_limit": gs["squad_team_limit"],
        "positions": positions,
    }


def optimize_squad(
    projections: list,
    rules: dict,
    owned_ids: set = None,
    sell_price_by_id: dict = None,
    bank: int = None,
    transfer_count: int = None,
) -> dict:
    players = projections
    idx = range(len(players))

    if owned_ids is not None:
        missing = owned_ids - {p["id"] for p in players}
        if missing:
            raise ValueError(f"owned player id(s) not found in projections: {missing}")

    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    squad = pulp.LpVariable.dicts("squad", idx, cat="Binary")
    start = pulp.LpVariable.dicts("start", idx, cat="Binary")
    captain = pulp.LpVariable.dicts("captain", idx, cat="Binary")

    teams = sorted(set(p["team"] for p in players))

    attack_excess = pulp.LpVariable.dicts("attack_excess", teams, lowBound=0)
    for t in teams:
        team_attackers = pulp.lpSum(
            start[i] for i in idx if players[i]["team"] == t and players[i]["element_type"] in ATTACKING_TYPES
        )
        prob += attack_excess[t] >= team_attackers - 1

    # projected_points is already summed across the projection horizon (see projections.py), so
    # the captain[i] term here is a simplifying "captained every gameweek of the horizon" bonus,
    # not a single-week decision - real weekly captaincy is picked fresh each gameweek later.
    prob += (
        pulp.lpSum(
            players[i]["projected_points"] * (start[i] + captain[i] + BENCH_WEIGHT * (squad[i] - start[i]))
            for i in idx
        )
        - STACK_PENALTY * pulp.lpSum(attack_excess[t] for t in teams)
    )

    prob += pulp.lpSum(squad[i] for i in idx) == rules["squad_size"]

    if owned_ids is None:
        prob += pulp.lpSum(players[i]["now_cost"] * squad[i] for i in idx) <= rules["budget"]
    else:
        # Cash available = bank + sell revenue of anyone transferred out (not their now_cost -
        # FPL taxes 50% of any profit on sale). New buys cost now_cost; kept owned players cost
        # nothing extra since they're already paid for.
        spend = pulp.lpSum(players[i]["now_cost"] * squad[i] for i in idx if players[i]["id"] not in owned_ids)
        sell_revenue = pulp.lpSum(
            sell_price_by_id[players[i]["id"]] * squad[i] for i in idx if players[i]["id"] in owned_ids
        )
        owned_sell_total = sum(sell_price_by_id[pid] for pid in owned_ids)
        prob += spend <= bank + owned_sell_total - sell_revenue

        if transfer_count is not None:
            prob += pulp.lpSum(1 - squad[i] for i in idx if players[i]["id"] in owned_ids) == transfer_count

    for t in teams:
        prob += pulp.lpSum(squad[i] for i in idx if players[i]["team"] == t) <= rules["team_limit"]

    for pos, bounds in rules["positions"].items():
        pos_idx = [i for i in idx if players[i]["element_type"] == pos]
        prob += pulp.lpSum(squad[i] for i in pos_idx) == bounds["squad_select"]
        prob += pulp.lpSum(start[i] for i in pos_idx) >= bounds["squad_min_play"]
        prob += pulp.lpSum(start[i] for i in pos_idx) <= bounds["squad_max_play"]

    prob += pulp.lpSum(start[i] for i in idx) == rules["starting_size"]
    for i in idx:
        prob += start[i] <= squad[i]
        prob += captain[i] <= start[i]
    prob += pulp.lpSum(captain[i] for i in idx) == 1

    # Never start an unavailable player. Never buy one fresh either - but an *owned* unavailable
    # player (e.g. picked up an injury since last week) can stay parked on the bench rather than
    # being forced out; the optimizer will naturally prefer to transfer them if it's worth it.
    for i in idx:
        if players[i]["status"] in UNAVAILABLE_STATUSES:
            prob += start[i] == 0
            if owned_ids is None or players[i]["id"] not in owned_ids:
                prob += squad[i] == 0

    status = prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Optimizer did not find an optimal solution: {pulp.LpStatus[status]}")

    squad_ids = [i for i in idx if squad[i].value() == 1]
    starting_ids = [i for i in idx if start[i].value() == 1]
    bench_ids = [i for i in squad_ids if i not in starting_ids]
    captain_id = next(i for i in idx if captain[i].value() == 1)
    vice_id = max(
        (i for i in starting_ids if i != captain_id),
        key=lambda i: players[i]["projected_points"],
    )

    result = {
        "squad": [players[i] for i in squad_ids],
        "starting_xi": [players[i] for i in starting_ids],
        "bench": sorted((players[i] for i in bench_ids), key=lambda p: p["projected_points"], reverse=True),
        "captain": players[captain_id],
        "vice_captain": players[vice_id],
        "total_cost": sum(players[i]["now_cost"] for i in squad_ids),
        "starting_xi_points": sum(players[i]["projected_points"] for i in starting_ids)
        + players[captain_id]["projected_points"],
    }

    if owned_ids is not None:
        new_ids = {p["id"] for p in result["squad"]}
        result["transfers_out"] = [p for p in projections if p["id"] in owned_ids - new_ids]
        result["transfers_in"] = [p for p in result["squad"] if p["id"] not in owned_ids]
        result["num_transfers"] = len(result["transfers_out"])

    return result
