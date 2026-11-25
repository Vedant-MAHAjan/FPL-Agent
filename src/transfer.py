"""Weekly transfer decision (step 4's numeric layer): given the currently owned squad, sweep
over possible transfer counts and pick whichever nets the highest points after the -4 hit for
any transfers beyond the free ones available. This is what should run weekly once a squad
actually exists - not the fresh fifteen-from-scratch build in run_optimizer.py.
"""

from optimizer import optimize_squad

HIT_COST = 4
MAX_TRANSFERS_CONSIDERED = 5  # beyond this you're better off wildcarding, not stacking hits
# Projected points (over the 3-GW horizon) an extra banked free transfer is worth next week:
# best 2-FT plan minus best 1-FT plan on our real squads, GW4-12 snapshots - mean 2.1, median 2.2.
# A move must beat this to be worth spending a free transfer that would otherwise roll over.
FT_ROLL_VALUE = 2.0
FT_CAP = 5


def sell_price(purchase_price: int, now_cost: int) -> int:
    """FPL sell-price rule: full refund on a loss, but only half of any profit (rounded down
    to the nearest £0.1m - safe since prices are already integers in tenths)."""
    if now_cost <= purchase_price:
        return now_cost
    profit = now_cost - purchase_price
    return purchase_price + profit // 2


def _departed_player_stub(owned: dict) -> dict:
    """A zero-projection, unavailable stand-in for an owned player who has dropped out of
    projections entirely (e.g. went out on loan and was removed from bootstrap upstream). Without
    this the optimizer raises on the missing id and the whole weekly pipeline crashes. The stub
    keeps the squad/position constraints intact and, being unavailable with 0 projected points,
    gets parked on the bench and sold as soon as a transfer is available - which is exactly the
    real-world handling ("you own a player who no longer exists in the game; sell him")."""
    return {
        "id": owned["id"],
        "web_name": owned.get("web_name", f"id{owned['id']}"),
        "team": owned["team"],
        "element_type": owned["element_type"],
        "now_cost": owned["purchase_price"],  # no live price; purchase price -> break-even sale
        "status": "u",  # unavailable: optimizer won't start it, lets an owned one be sold not forced
        "projected_points": 0.0,
    }


def _owned_context(projections: list, squad_state: dict) -> tuple:
    projections_by_id = {p["id"]: p for p in projections}
    # Any owned player missing from projections has departed the game (loan/transfer out, removed
    # upstream). Inject a zero-projection stub so the optimizer can sell them instead of crashing.
    stubs = [_departed_player_stub(p) for p in squad_state["squad"] if p["id"] not in projections_by_id]
    if stubs:
        projections = projections + stubs
        projections_by_id = {p["id"]: p for p in projections}
    owned_ids = {p["id"] for p in squad_state["squad"]}
    sell_price_by_id = {
        p["id"]: sell_price(p["purchase_price"], projections_by_id[p["id"]]["now_cost"])
        for p in squad_state["squad"]
    }
    return projections, owned_ids, sell_price_by_id


def best_transfer_plan(projections: list, rules: dict, squad_state: dict, free_transfers: int) -> dict:
    projections, owned_ids, sell_price_by_id = _owned_context(projections, squad_state)
    bank = squad_state["bank"]

    cap = min(MAX_TRANSFERS_CONSIDERED, free_transfers + 3)
    candidates = []
    for t in range(0, cap + 1):
        result = optimize_squad(
            projections, rules, owned_ids=owned_ids, sell_price_by_id=sell_price_by_id, bank=bank, transfer_count=t
        )
        hit = HIT_COST * max(0, t - free_transfers)
        candidates.append({
            "num_transfers": t,
            "hit": hit,
            "gross_points": result["starting_xi_points"],
            "net_score": result["starting_xi_points"] - hit,
            "roll_credit": roll_credit(free_transfers, t),
            "result": result,
        })
    return {"best": choose_plan(candidates), "all_candidates": candidates}


def roll_credit(free_transfers: int, used: int, ft_value: float = FT_ROLL_VALUE, cap: int = FT_CAP) -> float:
    """Value of the free transfers this plan leaves to roll into next week, beyond the 1 you'd get anyway."""
    carried = min(cap, max(0, free_transfers - used) + 1)
    return ft_value * (carried - 1)


def choose_plan(candidates: list) -> dict:
    """Highest net score plus the value of any free transfers left to roll. Ties go to fewer
    transfers."""
    return max(candidates, key=lambda c: (c["net_score"] + c.get("roll_credit", 0.0), -c["num_transfers"]))


def wildcard_plan(projections: list, rules: dict, squad_state: dict, free_transfer_plan: dict) -> dict:
    """What a wildcard would buy: the best squad reachable with up to 15 free transfers, against
    the best regular plan (free_transfer_plan = best_transfer_plan's output). gain is the
    projected-points edge over that plan after its hit - the number the chip decision needs."""
    projections, owned_ids, sell_price_by_id = _owned_context(projections, squad_state)
    result = optimize_squad(
        projections, rules, owned_ids=owned_ids, sell_price_by_id=sell_price_by_id,
        bank=squad_state["bank"], max_transfers=rules["squad_size"],
    )
    regular = free_transfer_plan["best"]
    return {
        "result": result,
        "num_transfers": result["num_transfers"],
        "gross_points": result["starting_xi_points"],
        "gain_vs_best_regular": result["starting_xi_points"] - regular["net_score"],
    }


def next_best_targets(projections: list, squad_state: dict, n: int = 8) -> list:
    """Non-owned players ranked by points-per-million, with a read on whether you could afford
    one today (bank + the cheapest sellable squad player in the same position).

    Deliberately a watchlist, not a multi-week sequencing plan: a "play this transfer in 3 weeks"
    plan computed off today's data has limited real value, since next week's actual fixtures/
    prices/team-news will be different and this whole pipeline gets rerun fresh anyway. What's
    actually useful ahead of time is knowing who's next in line, which this gives directly.
    """
    owned_ids = {p["id"] for p in squad_state["squad"]}
    bank = squad_state["bank"]
    projections_by_id = {p["id"]: p for p in projections}

    cheapest_sell_by_pos = {}
    for p in squad_state["squad"]:
        proj = projections_by_id.get(p["id"])
        if not proj:
            continue
        sp = sell_price(p["purchase_price"], proj["now_cost"])
        pos = p["element_type"]
        if pos not in cheapest_sell_by_pos or sp < cheapest_sell_by_pos[pos]:
            cheapest_sell_by_pos[pos] = sp

    candidates = [p for p in projections if p["id"] not in owned_ids and p["status"] not in ("i", "s", "u", "n")]
    ranked = sorted(candidates, key=lambda p: p["projected_points"] / (p["now_cost"] / 10), reverse=True)

    targets = []
    for p in ranked[:n]:
        available_funds = bank + cheapest_sell_by_pos.get(p["element_type"], 0)
        targets.append({
            "id": p["id"],
            "web_name": p["web_name"],
            "element_type": p["element_type"],
            "now_cost": p["now_cost"],
            "projected_points": p["projected_points"],
            "points_per_million": round(p["projected_points"] / (p["now_cost"] / 10), 3),
            "affordable_now": available_funds >= p["now_cost"],
            "shortfall": max(0, p["now_cost"] - available_funds),
        })
    return targets
