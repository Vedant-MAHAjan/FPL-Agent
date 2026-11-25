# FPL Agent

A personal decision-support tool for Fantasy Premier League. It doesn't play the game for you —
it hands you a concrete weekly recommendation (transfer, captain, chip call), with a stated
confidence level and the reasoning behind it, and you make the final call in the real FPL app.

Built for one user, one team, one season (2026/27). Zero ongoing cost: it only calls the free,
public FPL API and free news feeds — no subscriptions, no paid data.

## Status

**In-season.** Running live for one manager from GW1 onward. Every recommendation is logged
*before* the deadline and checked against the real outcome once the gameweek resolves — so the
question this project exists to answer (does the judgment layer actually beat a pure-numbers
baseline?) gets a concrete, weekly answer instead of a vibe.

## What it actually does

Every week it produces a **decision packet**:

```
GW7 Decision Packet — Deadline: Sat 20 Sep, 11:30 BST

TRANSFER: OUT Gabriel (ARS) → IN Van de Ven (TOT)
  Rationale: fixture swing — Arsenal's next 3 opponents get much harder
  Confidence: High — stats-only

CAPTAIN: Haaland (VC: Bruno Fernandes)
  Rationale: still the highest projected scorer, but City's back-to-back away games add
  real rotation risk this manager hasn't shown his hand on yet
  Confidence: Medium — judgment-based

CHIP: Hold
  Rationale: no double or blank gameweek in range yet; both remaining chip halves are
  worth more saved for when one appears

BENCH ORDER: Raya, Kadıoğlu, Gomez, Dubravka
```

Every recommendation gets a confidence tier:
- **High** — pure stats: projected points, fixture difficulty, nothing else in play.
- **Medium/Low** — stats got overridden or hedged because of something a spreadsheet can't
  see: an injury news item, a new manager reshuffling a defense, a role change after a transfer.

That distinction is tracked over the season to answer one question: do the judgment calls actually
earn their keep, or would a pure numbers-only approach have done just as well? Every recommendation
is logged before the gameweek and checked against what actually happened after — including what
would have happened with the alternative picks (different captain, the player who got dropped).

## How the agent knows your current squad

Once you've created your team in the real FPL app you have an entry ID (the number in your team's
URL). That ID lets the agent pull your real squad, bank, and chip usage directly from the public
FPL API — no login needed, just that ID.

## How a week actually works

1. **Pull fresh data.** Prices, fixtures, injury status — live from the official FPL API.
2. **Numeric pass.** A deterministic optimizer works out the highest-scoring transfer/squad
   available, respecting budget, banked transfers, and the points hit for extra transfers.
3. **News check.** A digest is pulled from general football and FPL-analyst feeds and filtered to
   anything touching your squad or a realistic transfer target.
4. **Judgment pass.** Whatever the digest surfaced is reasoned about against real context and the
   numeric pick is confirmed, adjusted, or vetoed — with a confidence tier. Anything the digest
   didn't flag stays at the numeric pass's default High confidence.
5. **You execute it.** The tool never touches your FPL account — you make the change yourself.

Every 3–4 gameweeks a separate pass looks further ahead at chip timing (Wildcard / Free Hit /
Bench Boost / Triple Captain) against fixture swings and your remaining chip inventory.

## Setup and running

Needs **Python 3.10+** (the code uses `X | None` type hints). Use a virtual environment so the
solver (`pulp`) is always available - a bare system `python3` usually won't have it.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Everything runs from the repo root with the venv's Python. Personal state (squad, decision log,
data snapshots) is written to `data/`, which is git-ignored.

| Command | What it does |
|---|---|
| `.venv/bin/python src/run_gw.py 12` | The weekly pipeline for GW12: scores past weeks, syncs the squad, projects, finds the best transfers, scans the pool |
| `.venv/bin/python src/wildcard.py 12` | Wildcard drafts scored against not playing it |
| `.venv/bin/python src/chip_planner.py 12` | Projected value of each chip in every gameweek until they expire |
| `.venv/bin/python src/news_digest.py` | Hard news (injuries, suspensions) for the squad and transfer targets |
| `.venv/bin/python src/ghost.py build 11` | Pick the top-10% manager panel used as a copy-the-leaders control |
| `.venv/bin/python src/backtest_*.py` | Re-run a backtest (clean sheets, recent form, minutes, captain choice) |

Tests are plain scripts with no network access - run any `src/*_test.py`, for example
`.venv/bin/python src/memory_test.py`. (`chip_planner_test.py` and `transfer_test.py` are live dry
runs against the API, not unit tests.)

## Extra signals it checks that are easy to miss by hand

- **Set-piece duty** — penalties/corners/free-kicks, surfaced when it changes.
- **Ownership / differentials** — high-scoring, low-owned players for weeks where rank matters.
- **Price-change risk** — early warning on players trending toward a rise or fall.
- **European fixture congestion** — flags squad players at clubs in Europe (rotation risk).
- **Double/blank gameweek detection** — catches weeks a club plays twice or not at all.
- **Transfer watchlist intelligence** — not-yet-owned targets checked in the same news pull.

## What's still genuinely manual

- **The judgment call itself** — deciding whether a piece of news is enough to override the numbers
  is a human-in-the-loop (or LLM-session) call each week, deliberately not fully automated.
- **Committing a transfer/chip** — nothing writes back to your real FPL team automatically.

## Why this isn't just "an AI picks your team"

Point projection from stats is a solved problem — several free tools already do it well. The part
that doesn't exist elsewhere is the second layer: taking that projection and asking "is there a
real reason to trust it less (or more) this week than the numbers alone suggest?", then tracking
honestly, week over week, whether that second layer actually adds anything over the plain numeric
baseline. If it turns out it doesn't by mid-season, that's a real, useful answer too — not a
failure to paper over.
