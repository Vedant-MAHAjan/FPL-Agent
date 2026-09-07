"""Weekly transfer decision (step 4's numeric layer): given the currently owned squad, sweep
over possible transfer counts and pick whichever nets the highest points after the -4 hit for
any transfers beyond the free ones available. This is what should run weekly once a squad
actually exists - not the fresh fifteen-from-scratch build in run_optimizer.py.
"""

from optimizer import optimize_squad

HIT_COST = 4
MAX_TRANSFERS_CONSIDERED = 5  # beyond this you're better off wildcarding, not stacking hits


def sell_price(purchase_price: int, now_cost: int) -> int:
    """FPL sell-price rule: full refund on a loss, but only half of any profit (rounded down
    to the nearest £0.1m - safe since prices are already integers in tenths)."""
    if now_cost <= purchase_price:
        return now_cost
    profit = now_cost - purchase_price
    return purchase_price + profit // 2


def best_transfer_plan(projections: list, rules: dict, squad_state: dict, free_transfers: int) -> dict:
    projections_by_id = {p["id"]: p for p in projections}
    owned_ids = {p["id"] for p in squad_state["squad"]}
    sell_price_by_id = {
        p["id"]: sell_price(p["purchase_price"], projections_by_id[p["id"]]["now_cost"])
        for p in squad_state["squad"]
        if p["id"] in projections_by_id
    }
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
            "result": result,
        })

    # Prefer fewer transfers on a tie (bank the free transfer rather than making a pointless
    # sideways move) - explicit tie-break, not just an accident of iteration order.
    best = max(candidates, key=lambda c: (c["net_score"], -c["num_transfers"]))
    return {"best": best, "all_candidates": candidates}


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
