"""Unit tests for run_gw's pure helpers - no API. Run: python src/run_gw_test.py
"""

from run_gw import unused_reason


def test_unused_when_benched_lately():
    assert unused_reason([90, 0, 0]) == "0 minutes in 2 of last 3"


def test_back_from_injury_is_not_unused():
    assert unused_reason([0, 0, 90]) is None


def test_one_missed_game_is_not_unused():
    assert unused_reason([90, 0, 45]) is None


def test_too_little_history():
    assert unused_reason([0]) is None


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
