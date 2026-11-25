"""Unit tests for the captain ownership tie-break - no API. Run: python src/captaincy_test.py
"""

from captaincy import choose_captain


def _c(name, pts, own):
    return {"id": name, "web_name": name, "projected_points": pts, "ownership": own}


def test_clear_best_projection_wins_regardless_of_ownership():
    assert choose_captain([_c("template", 22.0, 78.0), _c("diff", 20.5, 20.0)])["id"] == "template"


def test_within_noise_the_lower_owned_wins():
    assert choose_captain([_c("template", 21.1, 78.0), _c("diff", 20.9, 30.0)])["id"] == "diff"


def test_among_several_close_options_lowest_owned_wins():
    pick = choose_captain([_c("a", 20.0, 70.0), _c("b", 19.8, 25.0), _c("c", 19.6, 10.0), _c("d", 18.0, 1.0)])
    assert pick["id"] == "c"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
