"""Unit tests for the in-season chip values (bench boost / triple captain / free hit) on a synthetic
pool - no API. Run: python src/chip_values_test.py
"""

from chip_planner import chip_values, per_gw_points, team_fixture_counts
from optimizer_test import RULES, _owned, _pool


def _with_weeks(players, gws):
    # per_gw points: week 2 doubles everyone's week-1 value (a stand-in for a double gameweek)
    for p in players:
        p["per_gw"] = [{"gw": g, "points": p["projected_points"] * (2 if len(gws) > 1 and g == gws[1] else 1)} for g in gws]
    return players


def test_per_gw_points_reads_each_week():
    players = _with_weeks(_pool(), [12, 13])
    by_gw = per_gw_points(players)
    pid = players[0]["id"]
    assert by_gw[13][pid] == 2 * by_gw[12][pid]


def test_bench_boost_is_bench_points_and_doubles_with_a_double_week():
    players = _with_weeks(_pool(), [12, 13])
    squad = _owned(players)
    rows = chip_values(players, squad, RULES, range(12, 14))
    assert rows[0]["bench_boost"] > 0
    assert abs(rows[1]["bench_boost"] - 2 * rows[0]["bench_boost"]) < 0.2


def test_free_hit_value_is_best_week_minus_own_xi():
    players = _with_weeks(_pool(), [12])
    squad = _owned(players)
    rows = chip_values(players, squad, RULES, range(12, 13), free_hit_fn=lambda week: 999.0)
    assert rows[0]["free_hit"] == round(999.0 - rows[0]["own_xi"], 1)


def test_team_fixture_counts_flags_blanks_and_doubles():
    fixtures = [{"event": 12, "team_h": 1, "team_a": 2}, {"event": 12, "team_h": 1, "team_a": 3}]
    c = team_fixture_counts(fixtures, range(12, 13))[12]
    assert c[1] == 2 and c[2] == 1 and c.get(4, 0) == 0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
