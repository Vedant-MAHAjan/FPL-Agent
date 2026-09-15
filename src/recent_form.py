"""In-season projection blend.

projections.py runs on a rate_baseline; pre-season that baseline was last season's per-90 rates,
which is right when there's no live data but wrong once the season is underway (it keeps rating
last year's stars). This blends THIS season's per-90 rates (already cumulative in bootstrap - no
per-player element-summary calls needed) against a prior, weighted by this season's minutes: early
on the prior dominates, and it hands over to live data as the sample grows. It returns a drop-in
rate_baseline for build_multi_gw_projections, so projections.py itself is unchanged.

The prior is last season's rate for established players, and the position-average rate for players
with no reliable last-season PL sample (promoted/new signings) - so a hot 3-game sample from a
newly promoted defender is regressed toward league average, not trusted at face value.

(Game-by-game recency weighting via element-summary is a later refinement; the cumulative blend is
the high-value core.)
"""
from projections import RATE_STAT_FIELDS, _f, _player_rate, compute_position_averages

PRIOR_MINUTES = 600          # strength of the prior in minutes; regresses small samples while still weighting live form
MIN_LAST_SEASON_MINUTES = 300  # below this, last season isn't a reliable prior -> use position average


def completed_gameweeks(bootstrap: dict) -> int:
    return sum(1 for e in bootstrap.get("events", []) if e.get("finished"))


def _reliability_minutes(this_min, last_min, completed_gws):
    """A full-season-equivalent minutes figure for the shrink/starter factors, so a nailed starter
    this season isn't treated as a bench player just because a few GWs is little raw time."""
    fse_this = (this_min / completed_gws) * 38 if (this_min > 0 and completed_gws) else 0.0
    w = min(0.7, completed_gws / 10.0)
    prior = last_min if last_min > 0 else fse_this
    return w * fse_this + (1 - w) * prior


def build_blended_baseline(bootstrap: dict, preseason_baseline: dict | None) -> dict:
    """Return {'elements': [...]} whose rate fields are the this-season / prior blend, to be passed
    as `rate_baseline` to build_multi_gw_projections."""
    completed = max(1, completed_gameweeks(bootstrap))
    base_by_id = {p["id"]: p for p in preseason_baseline["elements"]} if preseason_baseline else {}
    pos_avg = compute_position_averages(bootstrap["elements"])  # this-season league averages, per position

    elements = []
    for cur in bootstrap["elements"]:
        base = base_by_id.get(cur["id"])
        this_min = _f(cur, "minutes")
        last_min = _f(base, "minutes") if base else 0.0
        use_last = base is not None and last_min >= MIN_LAST_SEASON_MINUTES
        rel_min = _reliability_minutes(this_min, last_min, completed)
        pos = cur["element_type"]

        el = {"id": cur["id"], "minutes": rel_min}
        for stat, (field, is_raw) in RATE_STAT_FIELDS.items():
            this_rate = _player_rate(cur, field, is_raw, this_min)
            prior_rate = _player_rate(base, field, is_raw, last_min) if use_last else pos_avg[pos][stat]
            if this_min <= 0:
                blended = prior_rate  # no live signal yet
            else:
                w = this_min / (this_min + PRIOR_MINUTES)
                blended = w * this_rate + (1 - w) * prior_rate
            # raw fields are read back as raw/minutes*90; set them so that recovers the blended rate
            el[field] = (blended * rel_min / 90) if (is_raw and rel_min > 0) else blended
        # remaining RATE_BASELINE_FIELDS: unused by scoring, kept sane (total_points must be non-zero
        # for anyone who has played so the new-to-PL low-confidence branch doesn't misfire)
        el["total_points"] = _f(cur, "total_points") + (_f(base, "total_points") if base else 0.0)
        el["expected_goals"] = _f(cur, "expected_goals")
        el["expected_assists"] = _f(cur, "expected_assists")
        elements.append(el)
    return {"elements": elements}
