"""Captain choice with an ownership tie-break.

Relative to the field, your gain from a player is (your multiplier - field effective ownership) x
his points, so swapping captain from A to B changes your expected gain by exactly B's points minus
A's: ownership cancels out of the expected comparison. What ownership changes is variance - a
low-owned captain moves your rank further either way. So expected points decide; only when the best
options are within noise does the lower-owned one win, buying upside at no expected cost (the way to
climb from a mid rank).
"""

# Projected points over the 3-GW horizon treated as a tie: ~0.17/GW, small next to how noisy a single
# week is (single-week rank correlation of projections vs points ~0.1 in the backtests). A judgment
# threshold, not a fitted one - revisit once the captain log has enough weeks.
CAPTAIN_TIE = 0.5


def choose_captain(candidates: list, tie: float = CAPTAIN_TIE) -> dict:
    """candidates: [{'id', 'web_name', 'projected_points', 'ownership'}] for the starting XI.
    Returns the lowest-owned candidate within `tie` of the top projection."""
    best = max(c["projected_points"] for c in candidates)
    close = [c for c in candidates if best - c["projected_points"] <= tie]
    return min(close, key=lambda c: (c["ownership"], -c["projected_points"]))


def captain_table(starting_xi: list, ownership_by_id: dict, n: int = 3) -> list:
    """Top n captain options from the XI with projection and ownership, best projection first."""
    rows = [{"id": p["id"], "web_name": p["web_name"], "projected_points": p["projected_points"],
             "ownership": ownership_by_id.get(p["id"], 0.0)} for p in starting_xi]
    return sorted(rows, key=lambda r: -r["projected_points"])[:n]
