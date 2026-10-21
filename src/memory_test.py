"""Unit tests for outcome scoring: auto-subs, vice-captain armband, and transfer hits. Pure logic on
synthetic data against a temp decisions log - no API. Run: python src/memory_test.py
"""

import json
import os
import tempfile

import memory
from memory import apply_auto_subs

# ids 1-2 GK, 10-15 DEF, 20-25 MID, 30-33 FWD
POS = {1: 1, 2: 1, **{i: 2 for i in range(10, 16)}, **{i: 3 for i in range(20, 26)},
       **{i: 4 for i in range(30, 34)}}
XI = [1, 10, 11, 12, 13, 20, 21, 22, 23, 30, 31]  # 4-4-2
BENCH = [2, 14, 24, 32]
ALL_PLAYED = {i: 90 for i in POS}


def test_no_subs_when_everyone_played():
    xi, subs = apply_auto_subs(XI, BENCH, ALL_PLAYED, POS)
    assert xi == XI and subs == [], subs


def test_first_eligible_bench_player_comes_on():
    mins = {**ALL_PLAYED, 21: 0}
    xi, subs = apply_auto_subs(XI, BENCH, mins, POS)
    assert subs == [(21, 14)], subs  # first outfield bench player in order, formation 5-3-2 is legal


def test_bench_player_who_didnt_play_is_skipped():
    mins = {**ALL_PLAYED, 21: 0, 14: 0}
    _, subs = apply_auto_subs(XI, BENCH, mins, POS)
    assert subs == [(21, 24)], subs


def test_formation_blocks_illegal_sub():
    # 3-5-2: losing a defender can't be covered by a midfielder (would leave 2 DEF).
    xi = [1, 10, 11, 12, 20, 21, 22, 23, 24, 30, 31]
    bench = [2, 25, 33, 13]
    mins = {**ALL_PLAYED, 12: 0}
    _, subs = apply_auto_subs(xi, bench, mins, POS)
    assert subs == [(12, 13)], subs


def test_goalkeeper_only_replaced_by_goalkeeper():
    mins = {**ALL_PLAYED, 1: 0}
    _, subs = apply_auto_subs(XI, BENCH, mins, POS)
    assert subs == [(1, 2)], subs


def _resolve(record, points, minutes):
    path = tempfile.mktemp(suffix=".jsonl")
    with open(path, "w") as f:
        f.write(json.dumps(record) + "\n")
    old = memory.DECISIONS_PATH
    memory.DECISIONS_PATH = path
    try:
        return memory.record_outcome(record["gw"], live_points_by_id=points, live_minutes_by_id=minutes,
                                     element_type_by_id=POS)["outcome"]
    finally:
        memory.DECISIONS_PATH = old
        os.remove(path)


def _record(**kw):
    rec = {"gw": 6, "horizon": [6, 8], "captain_id": 30, "vice_captain_id": 20, "starting_xi_ids": XI,
           "bench_ids": BENCH, "baseline_captain_id": 30, "baseline_vice_captain_id": 20,
           "baseline_starting_xi_ids": XI, "baseline_bench_ids": BENCH, "judgment_log": [],
           "resolved": False, "outcome": None}
    rec.update(kw)
    return rec


def test_baseline_hit_is_subtracted():
    points = {i: 2 for i in POS}
    o = _resolve(_record(baseline_points_hit=4), points, ALL_PLAYED)
    assert o["baseline_starting_xi_total"] == o["starting_xi_total"] - 4, o
    assert o["judgment_delta"] == 4, o


def test_judgment_hit_is_subtracted():
    points = {i: 2 for i in POS}
    o = _resolve(_record(transfer={"points_hit": 8}), points, ALL_PLAYED)
    assert o["judgment_delta"] == -8, o


def test_armband_passes_to_vice_when_captain_doesnt_play():
    points = {**{i: 2 for i in POS}, 30: 0, 20: 10}
    mins = {**ALL_PLAYED, 30: 0}
    o = _resolve(_record(), points, mins)
    j = o["judgment_scoring"]
    assert j["armband_id"] == 20, j
    # XI: 30 subbed off for 14 (2 pts); vice 20 doubled (+10)
    assert j["gross"] == 9 * 2 + 10 + 2 + 10, j


def test_no_minutes_means_no_autosubs_and_says_so():
    points = {i: 2 for i in POS}
    o = _resolve(_record(), points, None)
    assert o["judgment_scoring"]["autosubs_applied"] is False, o


def test_no_discussion_week_kept_out_of_judgment_metric():
    path = tempfile.mktemp(suffix=".jsonl")
    def rec(gw, delta, **kw):
        return {"gw": gw, "resolved": True, "outcome": {"starting_xi_total": 50 + delta,
                "baseline_starting_xi_total": 50, "judgment_delta": delta}, **kw}
    with open(path, "w") as f:
        f.write(json.dumps(rec(1, 5)) + "\n")
        f.write(json.dumps(rec(2, -19, no_agent_decision=True)) + "\n")
    old = memory.DECISIONS_PATH
    memory.DECISIONS_PATH = path
    try:
        c = memory.compute_season_comparison()
    finally:
        memory.DECISIONS_PATH = old
        os.remove(path)
    assert c["delta"] == 5 and c["judgment_wins"] == 1, c
    assert c["no_discussion_weeks"] == {2: -19}, c


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
