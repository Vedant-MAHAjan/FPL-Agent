"""Unit tests for wildcard draft scoring and the optimizer's must_include, on a synthetic pool - no
API. Run: python src/wildcard_test.py
"""

from optimizer import optimize_squad
from optimizer_test import RULES, _owned, _pool
from wildcard import score_squad


def test_score_squad_keeps_the_fifteen_and_picks_best_xi():
    players = _pool()
    squad = _owned(players)
    res = score_squad(squad, players, RULES)
    assert {p["id"] for p in res["squad"]} == squad
    assert len(res["starting_xi"]) == 11


def test_score_squad_tolerates_an_unavailable_player():
    players = _pool()
    squad = _owned(players)
    gone = next(p for p in players if p["id"] in squad and p["element_type"] == 4)
    gone["status"] = "u"
    res = score_squad(squad, players, RULES)
    assert gone["id"] not in {p["id"] for p in res["starting_xi"]}


def test_must_include_forces_players_in():
    players = _pool()
    weakest = min((p for p in players if p["element_type"] == 3), key=lambda p: p["projected_points"])
    res = optimize_squad(players, RULES, must_include={weakest["id"]})
    assert weakest["id"] in {p["id"] for p in res["squad"]}


def test_max_shared_forces_a_different_squad():
    players = _pool()
    first = {p["id"] for p in optimize_squad(players, RULES)["squad"]}
    second = {p["id"] for p in optimize_squad(players, RULES, max_shared=(first, 10))["squad"]}
    assert len(first & second) <= 10


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
