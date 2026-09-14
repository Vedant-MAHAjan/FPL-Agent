"""Unit tests for the chip-aware free-transfer derivation. Pure logic on synthetic history - no
API. Run: python src/squad_sync_test.py
"""

from squad_sync import _free_transfers

BOOT = {"game_settings": {"max_extra_free_transfers": 4}}  # cap = 5


def _hist(transfers_by_gw, chips=None):
    return {
        "current": [{"event": gw, "event_transfers": n} for gw, n in transfers_by_gw.items()],
        "chips": chips or [],
    }


def test_accrues_when_idle():
    # No transfers GW2..GW4 -> FT for GW5 should be 1 (GW2) +1 +1 +1 capped... trace: after GW2=2,
    # GW3=3, GW4=4. (GW1 is the free build, skipped.)
    ft = _free_transfers(_hist({1: 0, 2: 0, 3: 0, 4: 0}), BOOT)
    assert ft == 4, ft


def test_spending_one_each_week_stays_at_one():
    ft = _free_transfers(_hist({1: 0, 2: 1, 3: 1, 4: 1}), BOOT)
    assert ft == 1, ft


def test_wildcard_week_does_not_burn_ft():
    # Wildcard in GW3 with 10 transfers must NOT zero the bank. Idle GW2 -> 2; WC GW3 -> accrues to
    # 3 (no spend); idle GW4 -> 4. The old bug returned 1 here.
    hist = _hist({1: 0, 2: 0, 3: 10, 4: 0}, chips=[{"name": "wildcard", "event": 3}])
    assert _free_transfers(hist, BOOT) == 4, _free_transfers(hist, BOOT)


def test_freehit_week_does_not_burn_ft():
    hist = _hist({1: 0, 2: 1, 3: 11, 4: 0}, chips=[{"name": "freehit", "event": 3}])
    # GW2 spend1 ->1; FH GW3 -> accrue to 2; idle GW4 -> 3
    assert _free_transfers(hist, BOOT) == 3, _free_transfers(hist, BOOT)


def test_caps_at_five():
    ft = _free_transfers(_hist({g: 0 for g in range(1, 12)}), BOOT)
    assert ft == 5, ft  # cap = max_extra(4) + 1


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
