"""Form-grounded fixture difficulty.

The problem this fixes: the FPL API's fixture difficulty rating (team_h/a_difficulty) is a
near-static, pre-season seeding of team strength. Early in a season it does NOT reflect how teams
are ACTUALLY playing - a side tipped as "hard" (FDR 4) that has conceded in every game is an easy
fixture in reality, and vice versa. Ranking players on that static number is "one-level" thinking:
it trusts a label instead of checking whether the label still holds.

This module builds live attack/defence ratings from THIS season's finished results and turns them
into a difficulty multiplier that is blended with the static FDR - leaning on the static rating
while the sample is tiny and handing over to real form as games accumulate. It is deliberately
venue-aware (home vs away rates differ a lot) and position-aware (an attacker's difficulty is the
opponent's leakiness; a defender's is the opponent's threat).

Only free, keyless data: fixtures (finished scorelines) + bootstrap team list.
"""

# Shrinkage prior, in "games", pulling a small live sample toward the league average so a 3-game
# record isn't trusted at face value. ~a third of a season.
PRIOR_GAMES = 4.0

# How fast we hand over from static FDR to live form as games are played. At C completed
# gameweeks the live weight is min(FORM_WEIGHT_CAP, C / FORM_WEIGHT_FULL).
FORM_WEIGHT_FULL = 8.0   # ~full trust in live form by ~8 games (times the cap)
FORM_WEIGHT_CAP = 0.65   # never let form fully drown the static prior - keeps some stability

# Bound the live multiplier so one freak scoreline can't send a projection to the moon.
MULT_LO, MULT_HI = 0.75, 1.25
SENSITIVITY = 0.5  # how strongly a leaky/toothless opponent moves the multiplier

ATTACKING_TYPES = (3, 4)  # MID, FWD: difficulty = opponent leakiness
DEFENSIVE_TYPES = (1, 2)  # GK, DEF: difficulty = opponent threat


def _static_fdr_mult(fdr) -> float:
    """Kept identical to projections._fdr_mult so behaviour is unchanged when there's no live
    data - the two are the same curve on purpose (imported there to avoid drift)."""
    return 1 + (3 - fdr) * 0.075 if fdr is not None else 1.0


def build_team_ratings(fixtures: list, bootstrap: dict) -> dict:
    """Per-team, venue-split goals scored/conceded per game from finished fixtures, shrunk toward
    the league venue average. Returns a dict usable by `difficulty_multiplier`.
    """
    team_ids = [t["id"] for t in bootstrap["teams"]]
    acc = {t: {"h_gf": 0, "h_ga": 0, "h_n": 0, "a_gf": 0, "a_ga": 0, "a_n": 0} for t in team_ids}

    n_fix = 0
    tot_home_goals = 0  # goals scored by home sides (== goals conceded by away sides)
    tot_away_goals = 0  # goals scored by away sides
    for f in fixtures:
        if not f.get("finished"):
            continue
        hs, as_ = f.get("team_h_score"), f.get("team_a_score")
        if hs is None or as_ is None:
            continue
        h, a = f["team_h"], f["team_a"]
        acc[h]["h_gf"] += hs; acc[h]["h_ga"] += as_; acc[h]["h_n"] += 1
        acc[a]["a_gf"] += as_; acc[a]["a_ga"] += hs; acc[a]["a_n"] += 1
        n_fix += 1
        tot_home_goals += hs
        tot_away_goals += as_

    if n_fix == 0:
        league = {"h_scored": 1.5, "a_scored": 1.1}  # sane PL-ish fallback pre-season
    else:
        league = {"h_scored": tot_home_goals / n_fix, "a_scored": tot_away_goals / n_fix}
    # By identity, avg home goals scored == avg away goals conceded, and vice versa.
    league["a_conceded"] = league["h_scored"]
    league["h_conceded"] = league["a_scored"]

    def shrink(total, games, prior_rate):
        return (total + PRIOR_GAMES * prior_rate) / (games + PRIOR_GAMES)

    ratings = {}
    for t in team_ids:
        d = acc[t]
        ratings[t] = {
            "h_scored": shrink(d["h_gf"], d["h_n"], league["h_scored"]),
            "h_conceded": shrink(d["h_ga"], d["h_n"], league["h_conceded"]),
            "a_scored": shrink(d["a_gf"], d["a_n"], league["a_scored"]),
            "a_conceded": shrink(d["a_ga"], d["a_n"], league["a_conceded"]),
            "games": d["h_n"] + d["a_n"],
        }
    return {"teams": ratings, "league": league, "n_fixtures": n_fix}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _live_multiplier(pos: int, opponent_id: int, was_home: bool, ratings: dict) -> float:
    """Pure form-based multiplier (no static blend). Opponent plays at the venue opposite to the
    player's team: if the player is home, the opponent is away."""
    league, teams = ratings["league"], ratings["teams"]
    opp = teams.get(opponent_id)
    if opp is None:
        return 1.0
    opp_at_home = not was_home
    if pos in ATTACKING_TYPES:
        # easier when the opponent concedes more than a league-average side at this venue
        if opp_at_home:
            ratio = opp["h_conceded"] / (league["h_conceded"] or 1e-6)
        else:
            ratio = opp["a_conceded"] / (league["a_conceded"] or 1e-6)
        mult = 1 + SENSITIVITY * (ratio - 1)
    else:
        # defender/GK: harder when the opponent scores more than an average side at this venue
        if opp_at_home:
            ratio = opp["h_scored"] / (league["h_scored"] or 1e-6)
        else:
            ratio = opp["a_scored"] / (league["a_scored"] or 1e-6)
        mult = 1 - SENSITIVITY * (ratio - 1)
    return _clamp(mult, MULT_LO, MULT_HI)


def form_weight(completed_gws: int) -> float:
    return min(FORM_WEIGHT_CAP, max(0.0, completed_gws) / FORM_WEIGHT_FULL)


def difficulty_multiplier(pos: int, opponent_id: int, was_home: bool, fdr, ratings: dict,
                          completed_gws: int) -> float:
    """Blend the static FDR multiplier with the live, form-based one. Early season -> mostly FDR;
    as real games accumulate -> mostly form. This is the number that replaces a bare _fdr_mult."""
    static = _static_fdr_mult(fdr)
    if ratings is None or ratings.get("n_fixtures", 0) == 0:
        return static
    live = _live_multiplier(pos, opponent_id, was_home, ratings)
    w = form_weight(completed_gws)
    return (1 - w) * static + w * live


def make_difficulty_fn(ratings: dict, completed_gws: int):
    """A closure with the signature projections.project_player_horizon expects."""
    def fn(pos, opponent_id, was_home, fdr):
        return difficulty_multiplier(pos, opponent_id, was_home, fdr, ratings, completed_gws)
    return fn
