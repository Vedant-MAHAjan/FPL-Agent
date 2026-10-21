"""Unit tests for the optimizer's transfer constraints on a synthetic player pool - no API.
Run: python src/optimizer_test.py
"""

import random

from optimizer import optimize_squad

RULES = {
    "squad_size": 15, "starting_size": 11, "budget": 1000, "team_limit": 3,
    "positions": {
        1: {"squad_select": 2, "squad_min_play": 1, "squad_max_play": 1},
        2: {"squad_select": 5, "squad_min_play": 3, "squad_max_play": 5},
        3: {"squad_select": 5, "squad_min_play": 2, "squad_max_play": 5},
        4: {"squad_select": 3, "squad_min_play": 1, "squad_max_play": 3},
    },
}


def _pool(seed=7):
    rng = random.Random(seed)
    players, pid = [], 1
    for pos, n in ((1, 8), (2, 20), (3, 20), (4, 12)):
        for _ in range(n):
            players.append({"id": pid, "web_name": f"p{pid}", "team": pid % 20 + 1, "element_type": pos,
                            "now_cost": rng.randint(40, 90), "status": "a",
                            "projected_points": round(rng.uniform(2, 20), 1)})
            pid += 1
    return players


def _owned(players):
    # Deliberately weak but legal starting squad: the cheapest players per position.
    owned = []
    for pos, need in ((1, 2), (2, 5), (3, 5), (4, 3)):
        pos_players = sorted((p for p in players if p["element_type"] == pos), key=lambda p: p["projected_points"])
        owned += [p["id"] for p in pos_players[:need]]
    return set(owned)


def _solve(**kw):
    players = _pool()
    owned = _owned(players)
    sell = {p["id"]: p["now_cost"] for p in players if p["id"] in owned}
    return optimize_squad(players, RULES, owned_ids=owned, sell_price_by_id=sell, bank=100, **kw)


def test_wildcard_never_worse_than_fewer_transfers():
    wc = _solve(max_transfers=15)["starting_xi_points"]
    for t in (0, 1, 2, 3):
        assert wc >= _solve(transfer_count=t)["starting_xi_points"] - 1e-6, t


def test_max_transfers_is_an_upper_bound():
    res = _solve(max_transfers=2)
    assert res["num_transfers"] <= 2, res["num_transfers"]


def test_exact_count_still_exact():
    assert _solve(transfer_count=3)["num_transfers"] == 3


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
