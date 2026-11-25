"""Unit tests for the banked-free-transfer value in the transfer chooser - no API.
Run: python src/transfer_value_test.py
"""

from transfer import choose_plan, roll_credit


def _cand(t, net, ft):
    return {"num_transfers": t, "net_score": net, "roll_credit": roll_credit(ft, t)}


def test_small_gain_banks_the_transfer():
    # +1.5 for spending the 2nd free transfer is less than the ~2.0 it's worth rolled over
    best = choose_plan([_cand(1, 213.6, 2), _cand(2, 215.1, 2)])
    assert best["num_transfers"] == 1


def test_big_gain_spends_it():
    best = choose_plan([_cand(1, 213.6, 2), _cand(2, 217.0, 2)])
    assert best["num_transfers"] == 2


def test_at_the_cap_using_one_costs_nothing():
    # holding 5 (the cap): rolling all of them still leaves 5 next week, so using one is free
    assert roll_credit(5, 0) == roll_credit(5, 1) == 8.0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
