"""Free RSS news ingestion (component 2's stated data source per CLAUDE.md: "free RSS/public
feeds (BBC Sport, official club sites)" - not the paid X/Twitter API).

This does NOT automate the judgment call itself - judgment.py's docstring explains why that
stays a manual/hand-reasoned step (same reasoning as the chip planner). What this closes is the
step before that: instead of hand-searching the web from scratch for each player every week
(step 3's original manual pattern), pull a pre-filtered digest of recent articles that actually
mention a squad player, their club, or look like FPL-specific tips/analysis content, so the
judgment pass starts from real leads instead of nothing.

Two feed types, both verified live during development (real RSS 2.0 XML, no auth needed):
  - General football news (BBC) - best for direct events: injuries, transfers, manager changes.
  - FPL-specific analyst feeds (Fantasy Football Scout, Fantasy Football Pundit) - these publish
    distilled "what's working" analysis (captain picks, differentials, predicted lineups) rather
    than raw event reporting, which is a genuinely different and more directly useful signal than
    reverse-engineering strategy from ownership data - see the discussion that led here.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

MAX_ARTICLE_AGE_DAYS = 14  # some feeds (found during testing: Fantasy Football Pundit) serve
# months-old articles alongside current ones - without this, stale last-season "GW23 tips" posts
# would resurface as if they were current signal every single week.

FEEDS = {
    "bbc_football": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "bbc_premier_league": "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml",
    "fantasy_football_scout": "https://www.fantasyfootballscout.co.uk/feed/",
    "fantasy_football_pundit": "https://fantasyfootballpundit.com/feed/",
}

# Titles/summaries containing these look like FPL strategy content even when they don't name a
# specific squad player - filter_relevant()'s name-matching would miss "FPL Gameweek 7 Tips"
# entirely, since it doesn't mention any one player.
EXPERT_CONTENT_KEYWORDS = [
    "captain", "differential", "wildcard", "gameweek", "fpl tips", "bench boost",
    "triple captain", "free hit", "transfer target", "player to watch", "team news",
    "predicted lineup", "price rise", "price fall",
]


def fetch_feed(url: str) -> list:
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return [
        {
            "title": item.findtext("title") or "",
            "summary": item.findtext("description") or "",
            "link": item.findtext("link") or "",
            "published": item.findtext("pubDate") or "",
        }
        for item in root.findall(".//item")
    ]


def _is_recent(published: str, max_age_days: int) -> bool:
    if not published:
        return True  # no date given - don't silently drop it, let it through
    try:
        dt = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


def fetch_all_feeds(feeds: dict = None, max_age_days: int = MAX_ARTICLE_AGE_DAYS) -> list:
    """Fetches every configured feed, dedupes by link (BBC's general football feed and its
    Premier-League-specific feed both carry the same syndicated articles fairly often), and drops
    anything older than max_age_days (see MAX_ARTICLE_AGE_DAYS - verified live during development
    that at least one feed serves stale multi-month-old articles mixed in with current ones)."""
    feeds = feeds or FEEDS
    all_items = []
    seen_links = set()
    for name, url in feeds.items():
        try:
            for item in fetch_feed(url):
                if item["link"] in seen_links or not _is_recent(item["published"], max_age_days):
                    continue
                seen_links.add(item["link"])
                item["source"] = name
                all_items.append(item)
        except requests.RequestException as e:
            print(f"warning: could not fetch {name} ({url}): {e}")
    return all_items


def filter_relevant(items: list, names: list) -> dict:
    """name -> list of matching articles (case-insensitive substring match on title+summary).

    Deliberately simple and transparent rather than clever: a name that doesn't appear verbatim
    (a nickname, a surname-only headline that differs from the FPL web_name, etc.) will be
    missed. This is a first-pass filter to narrow down where to look, not a claim of exhaustive
    coverage - the manual judgment step should still sanity-check beyond just this digest for
    anything genuinely decision-critical.
    """
    matches = {name: [] for name in names}
    for item in items:
        haystack = f"{item['title']} {item['summary']}".lower()
        for name in names:
            if name.lower() in haystack:
                matches[name].append(item)
    return {name: arts for name, arts in matches.items() if arts}


def filter_expert_content(items: list, keywords: list = None) -> list:
    """Articles that read as FPL strategy content (captain picks, differentials, gameweek tips)
    regardless of whether they name a specific squad player - this is what systematizes the
    "check expert previews" step that was previously an occasional manual WebSearch (it's what
    caught the optimizer wrongly excluding Haaland during step 2's validation) into something
    that runs every digest pull instead of only when someone remembers to do it.
    """
    keywords = keywords or EXPERT_CONTENT_KEYWORDS
    matches = []
    for item in items:
        haystack = f"{item['title']} {item['summary']}".lower()
        if any(kw in haystack for kw in keywords):
            matches.append(item)
    return matches
