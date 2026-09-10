"""Weekly news digest: pull the free RSS feeds (general football news + FPL-specific analyst
feeds), filter for anything touching a squad player, their club, a realistic transfer target, or
reads as general FPL strategy content. Run this before the judgment pass (judgment_pass.py) - it
replaces "search the web with no starting point" with "here's what's actually been published
this week that's relevant," covering the full squad and watchlist rather than a hand-picked few.
"""

import glob
import json
import os

import memory
from news import fetch_all_feeds, filter_expert_content, filter_relevant
from optimizer import squad_rules_from_bootstrap
from projections import build_multi_gw_projections, load_preseason_rate_baseline
from transfer import next_best_targets
from fpl_api import get_fixtures

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def latest_bootstrap_path() -> str:
    return sorted(glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json")))[-1]


def build_digest(bootstrap: dict, state: dict, watchlist: list = None) -> dict:
    """Returns {player_matches, watchlist_matches, club_matches, expert_articles, items_pulled}.
    watchlist (optional) is a list of not-yet-owned target players (transfer.next_best_targets'
    output) to check alongside the owned squad, so a transfer target's news gets caught before a
    recommendation is made, not just after.
    """
    teams_by_id = {t["id"]: t["name"] for t in bootstrap["teams"]}
    squad_names = [p["web_name"] for p in state["squad"]]
    watchlist_names = [p["web_name"] for p in (watchlist or [])]
    team_names = sorted(set(teams_by_id[p["team"]] for p in state["squad"]))

    items = fetch_all_feeds()

    return {
        "items_pulled": len(items),
        "feeds_used": sorted(set(i["source"] for i in items)),
        "player_matches": filter_relevant(items, squad_names),
        "watchlist_matches": filter_relevant(items, watchlist_names),
        "club_matches": filter_relevant(items, team_names),
        "expert_articles": filter_expert_content(items),
    }


def _print_matches(title, matches):
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)
    if not matches:
        print("(no matches in today's feed pull)")
    for name, articles in matches.items():
        print(f"\n{name}:")
        for a in articles:
            print(f"  - {a['title']}  ({a['published']})")
            print(f"    {a['link']}")


def main():
    bootstrap_path = latest_bootstrap_path()
    with open(bootstrap_path) as f:
        bootstrap = json.load(f)
    state = memory.get_squad_state()

    fixtures = get_fixtures()
    decision_gw = state["as_of_gw"] + 1
    rate_baseline = load_preseason_rate_baseline(RAW_DIR, bootstrap_path)
    projections = build_multi_gw_projections(
        bootstrap, fixtures, start_gw=decision_gw, num_gws=3, rate_baseline=rate_baseline
    )
    watchlist = next_best_targets(projections, state, n=8)

    print("Fetching RSS feeds (general news + FPL analyst feeds)...")
    digest = build_digest(bootstrap, state, watchlist)
    print(f"Pulled {digest['items_pulled']} articles across {len(digest['feeds_used'])} feed(s): "
          f"{', '.join(digest['feeds_used'])}")

    _print_matches("SQUAD PLAYER MENTIONS", digest["player_matches"])
    _print_matches("TRANSFER WATCHLIST MENTIONS", digest["watchlist_matches"])
    _print_matches("CLUB MENTIONS", digest["club_matches"])

    print("\n" + "=" * 90)
    print("EXPERT FPL ANALYSIS (tips/captain/differential/preview-shaped content)")
    print("=" * 90)
    if not digest["expert_articles"]:
        print("(nothing keyword-matched this pull)")
    for a in digest["expert_articles"]:
        print(f"  - {a['title']}  ({a['source']}, {a['published']})")
        print(f"    {a['link']}")

    covered = len(digest["player_matches"]) + len(digest["watchlist_matches"])
    print(f"\n\n{covered} squad/watchlist player(s), {len(digest['club_matches'])} club(s), and "
          f"{len(digest['expert_articles'])} expert-analysis article(s) matched - these are the "
          "leads for the judgment pass; everything else defaults to a stats-only High-confidence call.")


if __name__ == "__main__":
    main()
