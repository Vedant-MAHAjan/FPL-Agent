"""Unit tests for the ghost top-10% panel - no API. Run: python src/ghost_test.py
"""

from ghost import panel_mean, select_panel


def _rows(ranks, points=50, hit=0):
    return [{"event": g, "overall_rank": r, "points": points, "event_transfers_cost": hit} for g, r in enumerate(ranks, 1)]


def test_selects_only_top_ranked_full_season_managers():
    h = {1: _rows([500, 400]), 2: _rows([500, 9000]), 3: _rows([100])}  # 3 missed GW2
    assert select_panel(h, as_of_gw=2, cutoff_rank=1000) == [1]


def test_panel_mean_is_net_of_hits():
    h = {1: _rows([1, 1, 1], points=60, hit=4), 2: _rows([1, 1, 1], points=40)}
    assert panel_mean(h, 3) == (48.0, 2)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
