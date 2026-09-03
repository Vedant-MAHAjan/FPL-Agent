# FPL Decision Agent — Project Brief

## What this is
A personal, zero-cost agent that gives concrete, weekly FPL decisions (transfers,
captaincy, chip timing) for the 2026/27 season. It is designed for a single user
for now. Season starts 21 Aug 2026; FPL game launch expected ~late July 2026.

## Explicit non-goal
This is NOT "LLM predicts FPL points." That space is saturated (FPL Review, LiveFPL,
Fix, countless Kaggle models already do point-projection optimization well). Do not
rebuild that. The differentiator is the judgment layer described below.

## Core architecture: hybrid, not LLM-only

### 1. Numeric baseline layer (deterministic, not LLM)
- Simple linear optimizer (PuLP or equivalent) over point projections.
- Projections derived from underlying stats: xG, xA, minutes trend, fixture
  difficulty — pulled from the free, keyless official FPL API.
- This layer alone should already produce a sane suggested XV/transfer — validate
  it against 2–3 independent expert previews before adding anything on top.

### 2. Judgment layer (the actual "agent," LLM-driven)
- Takes the optimizer's suggested move + qualitative signals the optimizer can't
  quantify: team news, presser quotes about rotation/fitness, tactical system
  changes, new-signing bedding-in risk.
- Source for qualitative signals: free RSS/public feeds (BBC Sport, official club
  sites) — NOT the X/Twitter API (now paid, explicitly ruled out to preserve the
  zero-cost constraint).
- Action space per gameweek: CONFIRM the optimizer's pick, ADJUST it, or VETO it —
  with a stated rationale and an explicit confidence tier:
  - "High confidence" = stats-grounded, no qualitative override
  - "Medium/Low confidence" = judgment-based, news-driven, unconfirmed signal
- OPEN DECISION (resolve before building this layer): should the judgment layer
  be restricted to confirm/veto only (safer, easier to score later), or allowed to
  propose a transfer the optimizer didn't rank highly (higher ceiling, harder to
  attribute credit/blame during evaluation)? Default to confirm/veto-only unless
  a reason emerges to widen it.

### 3. Persistent memory (markdown/JSON log, same pattern as the team's internal
   code-reviewer agent — file-based feedback storage)
- Logs every past recommendation, its confidence tier, and the actual outcome
  once the gameweek resolves.
- Tracks two things over the season:
  a) Calibration by confidence tier (are "High" calls actually right more often
     than "Medium"? — Brier-score-style, checkable, not vibes)
  b) Squad-state history: chips used, free transfers banked, team value trend —
     so each week's call is grounded in actual season trajectory, not stateless.

### 4. Season-level chip planner (long-horizon reasoning, separate cadence)
- Runs every 3–4 gameweeks, not weekly.
- Reasons about wildcard / bench-boost / triple-captain / free-hit timing as a
  sequential decision tied to fixture swings, double/blank gameweek risk (these
  get announced mid-season via cup-round scheduling), and the user's specific
  remaining chip inventory.
- This is the piece with no real existing-tool equivalent — most tools give
  point-in-time advice, not season-long chip sequencing tied to personal state.

## Weekly output format (the actual deliverable — must always be this concrete,
never vaguer than this)

```
GW7 Decision Packet — Deadline: Sat 20 Sep, 11:30 BST

TRANSFER: OUT Gabriel (ARS) → IN Van de Ven (TOT)
  Rationale: [fixture swing / news signal / stat trend]
  Confidence: [High | Medium | Low] — [stats-only | judgment-based]

CAPTAIN: [Player] (VC: [Player])
  Rationale: ...
  Confidence: ...

CHIP: [Hold | Play X]
  Rationale: ...

BENCH ORDER: [optimizer output, or override + reason]
```

## Data sources (all free, keyless — do not introduce paid APIs)
- Official FPL API: `fantasy.premierleague.com/api/` — bootstrap-static, fixtures,
  entry/{id}, entry/{id}/history, element-summary/{id}. No auth required. No CORS
  support — must be called from a backend, not directly from a browser.
- Team news: free RSS/public feeds (BBC Sport, official club sites).
- Explicitly excluded: X/Twitter API (paid), any paid sports-data API.

## Success metric (must stay unambiguous, checkable against real outcomes)
- Per-recommendation: actual gameweek outcome vs. the road not taken.
- Per-confidence-tier: calibration accuracy over the season (does "High" beat
  "Medium" in hit rate, tracked like a Brier score).
- Season-level: cumulative points/rank vs. a numeric-only baseline (optimizer
  alone, no judgment layer) — this is the real test of whether the judgment
  layer earns its complexity, not just the FPL rank number in isolation.

## Build order (do not skip steps to get to "the agent" faster)
1. Pull `bootstrap-static` + own `entry/{id}/history` (if applicable). Confirm
   data shapes; check whether prices are live or still preseason placeholders.
2. Build the numeric optimizer alone. Validate its GW1 suggestion against 2–3
   published expert previews. If this baseline isn't already reasonable,
   fix this before adding the LLM layer — don't paper over a bad baseline with
   a fancier judgment layer on top.
3. Add the judgment layer as a single manual test: hand-prompt against real
   current team news for 3–4 real draft picks. Confirm it produces a genuinely
   non-obvious override, not just prose restating the optimizer's own output.
4. From GW1 onward: run live for one user, log every
   recommendation + outcome from week one. By GW10–12 there should be enough
   resolved calls to know concretely whether the judgment layer beats the
   numeric-only baseline — that is the actual question this project exists
   to answer, not "does it feel useful."

## Why this design (context for future sessions, don't re-litigate)
- This followed a prior project (an AI Dungeon Master for Indian mythology)
  whose real flaw was structural: the story's ending was fixed, so "does
  agency matter" could never be tested until the whole thing was built.
  This project is designed to avoid that specifically — every recommendation
  resolves against a real, external, known outcome within 7 days. No waiting
  a season to find out if the design premise holds.
- Pure LLM-only point-optimization was explicitly rejected as a design
  because it would just be a worse version of existing solved tools
  (FPL Review, LiveFPL, Fix already do this well via optimizers). The LLM's
  job is judgment on qualitative signals, not point arithmetic.
