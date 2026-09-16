"""Unit tests for form-grounded fixture difficulty. Pure logic on synthetic fixtures - no API.
Run: python src/opponent_strength_test.py
"""

import opponent_strength as os_


def _bootstrap(team_ids):
    return {"teams": [{"id": t, "short_name": f"T{t}"} for t in team_ids]}


def _fix(gw, h, a, hs, as_, finished=True):
    return {"event": gw, "team_h": h, "team_a": a, "team_h_score": hs, "team_a_score": as_,
            "finished": finished, "team_h_difficulty": 3, "team_a_difficulty": 3}


def test_leaky_opponent_boosts_attacker():
    # Team 2 concedes heavily at home (leaky); an attacker facing them AWAY (opp at home) should
    # get a multiplier > 1. Team 3 is stingy at home -> multiplier < 1.
    fixtures = [
        _fix(1, 2, 9, 0, 4), _fix(2, 2, 10, 1, 3),   # team 2 home: conceded 7 in 2
        _fix(1, 3, 9, 1, 0), _fix(2, 3, 10, 2, 0),   # team 3 home: conceded 0 in 2
    ]
    r = os_.build_team_ratings(fixtures, _bootstrap([2, 3, 9, 10]))
    # attacker (pos 4) of some away team facing team 2 (team2 at home) -> easy
    easy = os_._live_multiplier(4, opponent_id=2, was_home=False, ratings=r)
    hard = os_._live_multiplier(4, opponent_id=3, was_home=False, ratings=r)
    assert easy > 1.0, easy
    assert hard < 1.0, hard
    assert easy > hard


def test_defender_inverts_on_opponent_attack():
    # Team 5 scores a lot AND defends tight at home; the other games set a lower league baseline so
    # team 5 stands out. A DEFENDER facing them (opp at home) is penalised (<1); an ATTACKER facing
    # their tight defence is also < 1 (judged on the opponent's defence, not attack).
    fixtures = [
        _fix(1, 5, 6, 5, 1), _fix(2, 5, 7, 4, 0),   # team5 home: scored 9, conceded 1
        _fix(3, 6, 7, 1, 1), _fix(4, 7, 6, 2, 1), _fix(5, 8, 6, 1, 2),  # baseline
    ]
    r = os_.build_team_ratings(fixtures, _bootstrap([5, 6, 7, 8]))
    defender = os_._live_multiplier(2, opponent_id=5, was_home=False, ratings=r)  # facing big attack
    assert defender < 1.0, defender
    attacker = os_._live_multiplier(4, opponent_id=5, was_home=False, ratings=r)  # facing tight defence
    assert attacker < 1.0, attacker


def test_venue_awareness():
    # Team 7 leaks at home (concedes 6 in 2) but is solid away (concedes 0 in 2). An attacker's
    # ease should differ by which venue the opponent is playing. Extra games set a real baseline.
    fixtures = [
        _fix(1, 7, 9, 1, 3), _fix(2, 7, 10, 1, 3),   # team7 HOME: conceded 6
        _fix(3, 8, 7, 0, 1), _fix(4, 11, 7, 0, 1),   # team7 AWAY: conceded 0
        _fix(5, 9, 10, 2, 1), _fix(6, 10, 9, 1, 1),  # baseline
    ]
    r = os_.build_team_ratings(fixtures, _bootstrap([7, 8, 9, 10, 11]))
    facing_home = os_._live_multiplier(4, opponent_id=7, was_home=False, ratings=r)  # opp at home (leaky)
    facing_away = os_._live_multiplier(4, opponent_id=7, was_home=True, ratings=r)   # opp away (solid)
    assert facing_home > facing_away, (facing_home, facing_away)


def test_blend_leans_static_early():
    # With very few fixtures the form weight is small, so the blended multiplier stays close to the
    # static FDR multiplier (here FDR=1 -> static 1.15).
    fixtures = [_fix(1, 2, 9, 0, 5)]  # one lopsided result
    r = os_.build_team_ratings(fixtures, _bootstrap([2, 9]))
    static = os_._static_fdr_mult(1)
    blended = os_.difficulty_multiplier(4, opponent_id=2, was_home=False, fdr=1, ratings=r, completed_gws=1)
    assert abs(blended - static) < abs(os_._live_multiplier(4, 2, False, r) - static), \
        (blended, static)


def test_form_weight_grows_and_caps():
    assert os_.form_weight(0) == 0.0
    assert os_.form_weight(4) == 0.5
    assert os_.form_weight(100) == os_.FORM_WEIGHT_CAP  # capped, never fully drowns the prior


def test_clamped_and_no_data_is_static():
    # No finished fixtures -> ratings empty -> falls back exactly to static FDR.
    r = os_.build_team_ratings([], _bootstrap([1, 2]))
    assert r["n_fixtures"] == 0
    assert os_.difficulty_multiplier(4, 2, False, fdr=2, ratings=r, completed_gws=0) == os_._static_fdr_mult(2)
    # Extreme leak still clamps.
    fixtures = [_fix(1, 2, 9, 0, 10)]
    r2 = os_.build_team_ratings(fixtures, _bootstrap([2, 9]))
    m = os_._live_multiplier(4, 2, False, r2)
    assert os_.MULT_LO <= m <= os_.MULT_HI, m


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
