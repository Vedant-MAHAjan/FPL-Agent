# FPL Agent

A personal, zero-cost decision-support agent for Fantasy Premier League (2026/27 season).

It doesn't play the game for you. Each week it hands you one concrete recommendation —
transfer, captain, chip call — with a stated confidence level and the reasoning behind it,
and you make the final call in the real FPL app.

The point of the project isn't "AI predicts FPL points" — that's already solved by tools like
FPL Review and LiveFPL. The differentiator is a **judgment layer** on top of a numeric optimizer:
a second pass that weighs the qualitative signals a spreadsheet can't (team news, a new manager,
a changed role) and either confirms, adjusts, or vetoes the numbers — then logs every call and
checks it against what actually happened, to find out whether that judgment layer earns its keep.

See [CLAUDE.md](CLAUDE.md) for the full design brief.

> **Status:** building in public, one layer at a time. This README grows as the pieces land.

## Stack

- Python 3
- [`requests`](https://pypi.org/project/requests/) — official keyless FPL API
- [`PuLP`](https://pypi.org/project/PuLP/) — linear optimizer for squad selection

```bash
pip install -r requirements.txt
```
