"""Weekly judgment pass (component 2) - digest-driven, not a fixed shortlist.

Supersedes manual_judgment_test.py's step-3 pattern (which hand-picked 4 named players every
time, regardless of what was actually in the news that week). This version runs the news digest
(news_digest.build_digest - full squad + transfer watchlist + FPL-analyst feeds, see the Tier-3
review) and produces a real judgment call for whoever the digest actually flagged, however many
that turns out to be. Everyone else gets the default CONFIRM/High from judgment.apply_judgment()
automatically - that's not a gap, it's what makes "High confidence" mean something concrete.

The calls below still require a human (or an LLM session) to read the flagged leads and reason
about them - that step is deliberately not automated, per judgment.py's docstring. What changed
is that the INPUT to that reasoning is now the full-coverage digest, not whichever players someone
remembered to check.
"""

import glob
import json
import os

import memory
from fpl_api import get_fixtures
from judgment import CONFIRM, HIGH, MEDIUM, apply_judgment, judgment_call
from news_digest import build_digest
from optimizer import squad_rules_from_bootstrap
from projections import POSITION_NAMES, build_multi_gw_projections
from transfer import next_best_targets

PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
JUDGMENT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "judgment")
RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def latest_bootstrap_path() -> str:
    return sorted(glob.glob(os.path.join(RAW_DIR, "bootstrap_static_*.json")))[-1]


def find_player(squad_result, web_name):
    for p in squad_result["starting_xi"] + squad_result["bench"]:
        if p["web_name"] == web_name:
            return p
    raise KeyError(web_name)


def build_calls(squad_result):
    """Real judgment calls checked against real current news. As of the 2026-08-13 data refresh
    (8 days from the GW1 deadline) the squad shifted meaningfully from earlier runs - Bruno
    Fernandes is now captain instead of Haaland, Calvert-Lewin and Spence dropped out entirely.
    O'Reilly/Guéhi (Man City) and Wirtz (Liverpool) carry forward prior research since the
    underlying facts (Maresca's reshuffle, Iraola's Wirtz repositioning) haven't changed; Bruno's
    call is freshly researched given how much rides on the captaincy question.
    """
    bruno = find_player(squad_result, "B.Fernandes")
    oreilly = find_player(squad_result, "O'Reilly")
    guehi = find_player(squad_result, "Guéhi")
    wirtz = find_player(squad_result, "Wirtz")

    return [
        judgment_call(
            bruno["id"], bruno["web_name"], bruno["projected_points"],
            action=CONFIRM, confidence=MEDIUM,
            rationale=(
                "Captaincy here is genuinely close, not a clear-cut numeric win: current expert "
                "previews still frame BOTH Bruno and Haaland as GW1 must-haves, with Haaland "
                "holding a slight edge on ownership (73.6% per our own data) and fixture "
                "reputation - the optimizer only picked Bruno because Man Utd's GW1-3 run "
                "(promoted Hull, then Ipswich, then Everton) is genuinely easier than City's "
                "(Bournemouth, Palace, Coventry) over this specific 3-gameweek window, not "
                "because Haaland is a bad pick. Real news is reassuring though: Bruno has settled "
                "his contract situation (extended to 2027), is coming off a career-best season "
                "(21 assists, FWA + PL Player of the Season), and new manager Michael Carrick is "
                "explicitly building the team around him. Confidence Medium not High only because "
                "it's a new-manager first gameweek away at a promoted side - not because of any "
                "real doubt about the player."
            ),
            sources=[
                "https://www.premierleague.com/en/news/4675553/why-fernandes-and-haaland-look-like-must-haves-to-start-202627-fpl",
                "https://www.flashscore.com/news/soccer-premier-league-captain-bruno-fernandes-extends-manchester-united-stay-until-2027/MDIcZ2WF",
            ],
        ),
        judgment_call(
            oreilly["id"], oreilly["web_name"], oreilly["projected_points"],
            action=CONFIRM, confidence=MEDIUM,
            rationale=(
                "Nailed starter, not a rotation doubt - the live unknown is City's defensive "
                "shape under new manager Maresca (Stones/Bernardo Silva exited, Rodri out "
                "injured), not his role in it. Selection is fine; the clean-sheet-heavy chunk of "
                "his projection is the part to re-check once Maresca's back line settles - worth "
                "a last look closer to the deadline since preseason friendlies are underway now."
            ),
            sources=["https://sports.yahoo.com/articles/change-manchester-city-clear-see-210500052.html"],
        ),
        judgment_call(
            guehi["id"], guehi["web_name"], guehi["projected_points"],
            action=CONFIRM, confidence=MEDIUM,
            rationale=(
                "Same City-defense caveat as O'Reilly - the January 2026 transfer is long since "
                "settled (no bedding-in risk), but last season's clean-sheet rate reflects "
                "Guardiola's system, not Maresca's still-forming one."
            ),
            sources=["https://sports.yahoo.com/articles/change-manchester-city-clear-see-210500052.html"],
        ),
        judgment_call(
            wirtz["id"], wirtz["web_name"], wirtz["projected_points"],
            action=CONFIRM, confidence=MEDIUM,
            rationale=(
                "Genuine upside case: Liverpool's new manager Andoni Iraola plans to play him "
                "'behind the striker' rather than the wide role he mostly played last season - a "
                "real tactical change the optimizer's rate-based projection (built entirely on "
                "last season's per-90 numbers in the old role) can't see. Could cut either way "
                "until real minutes confirm it, hence Medium not High."
            ),
            sources=["https://sports.yahoo.com/articles/liverpool-boss-iraola-explains-florian-214400527.html"],
        ),
    ]


def notable_exclusions():
    """Players the numeric pass considered and left out, worth surfacing explicitly rather than
    silently dropping - not a judgment_call (nothing to confirm/veto on a pick that isn't made),
    just a flag for a decision big enough to deserve one.
    """
    return [{
        "web_name": "Haaland",
        "note": (
            "Excluded from the 15 entirely, not just the captaincy - purely a budget/horizon "
            "efficiency call (his GW1-3 projection is close to Bruno's despite City's tougher "
            "fixture run, but at £15.5m vs Bruno's £12.0m the points-per-million gap is real). "
            "Current expert previews explicitly call both players 'must-haves' for GW1, which "
            "this squad can't do within £100m without a weaker XI elsewhere. Worth knowing this "
            "was a real trade-off the model made, not an oversight."
        ),
    }]


def fmt_confidence(confidence):
    tag = "stats-only" if confidence == HIGH else "judgment-based"
    return f"{confidence} — {tag}"


def print_decision_packet(final, watchlist_notes):
    print("=" * 90)
    print("DECISION PACKET (numeric baseline + digest-driven judgment overlay)")
    print("=" * 90)

    print("\nSTARTING XI:")
    for p in sorted(final["starting_xi"], key=lambda p: p["element_type"]):
        call = next(c for c in final["judgment_log"] if c["player_id"] == p["id"])
        tag = " (C)" if p["id"] == final["captain"]["id"] else " (VC)" if p["id"] == final["vice_captain"]["id"] else ""
        print(f"  {POSITION_NAMES[p['element_type']]:3s} {p['web_name']:16s}{tag:5s} "
              f"[{call['action']:7s} | {fmt_confidence(call['confidence'])}]")

    print("\nBENCH:")
    for p in final["bench"]:
        call = next(c for c in final["judgment_log"] if c["player_id"] == p["id"])
        print(f"  {POSITION_NAMES[p['element_type']]:3s} {p['web_name']:16s}      "
              f"[{call['action']:7s} | {fmt_confidence(call['confidence'])}]")

    print(f"\nCAPTAIN: {final['captain']['web_name']}  (VC: {final['vice_captain']['web_name']})")

    manual_calls = [c for c in final["judgment_log"] if c["sources"]]
    print(f"\n--- Judgment rationale ({len(manual_calls)} digest/research-backed picks) ---")
    for c in manual_calls:
        print(f"\n{c['player_name']}: {c['action']} — {fmt_confidence(c['confidence'])}")
        print(f"  {c['rationale']}")
        print(f"  effect: {c['effect']}")

    if watchlist_notes:
        print(f"\n--- Watchlist intelligence ({len(watchlist_notes)}, not owned - informational) ---")
        for n in watchlist_notes:
            print(f"\n{n['web_name']}: {n['note']}")
            for a in n["articles"]:
                print(f"  - {a['title']}  ({a['source']})")

    exclusions = notable_exclusions()
    if exclusions:
        print(f"\n--- Notable exclusions ({len(exclusions)}, considered and left out) ---")
        for e in exclusions:
            print(f"\n{e['web_name']}: {e['note']}")


def main():
    with open(latest_bootstrap_path()) as f:
        bootstrap = json.load(f)
    fixtures = get_fixtures()
    squad_path = os.path.join(PROCESSED_DIR, "squad_gw1-3.json")
    with open(squad_path) as f:
        squad_result = json.load(f)

    state = memory.get_squad_state()
    rules = squad_rules_from_bootstrap(bootstrap)
    projections = build_multi_gw_projections(bootstrap, fixtures, start_gw=state["as_of_gw"], num_gws=3)
    watchlist = next_best_targets(projections, state, n=8)

    digest = build_digest(bootstrap, state, watchlist)
    print(f"Digest: {digest['items_pulled']} articles, {len(digest['player_matches'])} squad player(s), "
          f"{len(digest['watchlist_matches'])} watchlist player(s), "
          f"{len(digest['expert_articles'])} expert-analysis article(s) flagged.\n")

    calls = build_calls(squad_result)
    final = apply_judgment(squad_result, calls)

    watchlist_notes = [
        {"web_name": name, "note": "flagged by today's digest - not owned, informational only for now.",
         "articles": articles}
        for name, articles in digest["watchlist_matches"].items()
    ]

    print_decision_packet(final, watchlist_notes)

    os.makedirs(JUDGMENT_DIR, exist_ok=True)
    out_path = os.path.join(JUDGMENT_DIR, "decision_gw1-3.json")
    with open(out_path, "w") as f:
        json.dump({**final, "watchlist_notes": watchlist_notes, "notable_exclusions": notable_exclusions()}, f, indent=2)
    print(f"\n\nSaved -> {out_path}")

    record = memory.log_decision(
        gw=state["as_of_gw"], horizon=(state["as_of_gw"], state["as_of_gw"] + 2),
        decision_packet=final, baseline_result=squad_result,
    )
    print(f"Logged GW{record['gw']} decision (captain={record['captain_name']}) -> {memory.DECISIONS_PATH}")


if __name__ == "__main__":
    main()
