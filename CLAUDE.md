# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A phased experiment to answer one question: **can Claude beat Polymarket?**

Each phase adds one layer of complexity. Real Polymarket outcomes are the feedback signal. Paper trading (simulated bets) is used throughout — no real money, but real market prices and real resolutions. The experiment will be written up as a blog post.

This is foremost a learning project. When developing, always recommend and refer to tech docs, blogs, and concepts that will help the author understand the direction and validate decisions.

## The experiment loop

1. Select ~100 markets ending within 1 week
2. Claude evaluates each market — outputs a probability and confidence
3. Simulate a flat bet on every market where edge (Claude % − market %) ≥ 10%
4. Wait for resolution, record outcome, calculate P&L
5. Measure ROI and calibration for the phase
6. Add one complexity layer, repeat

**P&L is the signal. Everything else is noise.**

## Phases

| Phase | What changes | Hypothesis |
|---|---|---|
| 1 | Baseline: title + description + yes price | Does Claude have any edge at all? |
| 2 | Extended thinking | Does deeper reasoning improve calibration? |
| 3 | Real-time news retrieval (RSS + ChromaDB) | Does current information help? |
| 4 | Multi-agent debate (Yes proposer + No proposer + Judge) | Does structured disagreement reduce overconfidence? |

Each phase is one isolated change so the P&L delta cleanly attributes improvement.

## Market selection criteria

Not all markets are worth betting on. Apply these filters when selecting markets for a phase:

- `end_date` between 2–7 days from now (fast feedback, enough time to "bet")
- `liquidity` > $5,000 (thin markets have unreliable prices)
- `yes_price` between 10¢ and 90¢ (extremes have little room for edge)
- Binary only (Yes/No outcomes) — multi-outcome markets excluded
- `abs(claude_prob - yes_price) >= 10%` — only bet where there is meaningful edge

## Bet simulation logic

- **Direction**: bet Yes if Claude is above market, No if below
- **Size**: flat $10 per bet (keeps signal clean, separates model quality from bet sizing)
- **P&L**: `(outcome * (1 - entry_price) - (1 - outcome) * entry_price) * bet_size`

## Segment analysis

After each phase resolves, analyse performance across these dimensions to find Claude's systematic biases. Use findings to debias the prompt in the next phase.

| Segment | What to look for |
|---|---|
| By tag (politics, crypto, sports, etc.) | Domain-specific over/underestimation |
| By confidence level | Does high confidence actually predict accuracy? |
| By edge size | Are big edges real signal or overconfidence? |
| By liquidity bucket | Does Claude do better on thin vs efficient markets? |
| By phase | Core comparison — did added complexity help? |

**Calibration** is the key metric: if Claude says 70% on 20 markets, ~14 should resolve Yes. The standard measure is **Brier score** (lower = better). Read: [Brier score explainer](https://en.wikipedia.org/wiki/Brier_score) and Tetlock's *Superforecasting* for the conceptual foundation.

The actionable loop: find a systematic bias (e.g. "Claude overestimates crypto by 15%"), encode a correction into the next phase's prompt, measure if Brier score improves.

## SQLite schema (`data/evaluations.db`)

`evaluations` table — one row per market evaluation:

| Column | Purpose |
|---|---|
| `market_id` | PK |
| `market_title` | |
| `yes_price` | Polymarket price at evaluation time (0–100) |
| `claude_prob_yes` | Claude's probability estimate (0–100) |
| `confidence` | Claude's self-reported confidence (0–100) |
| `reasoning` | Claude's step-by-step reasoning |
| `bet_direction` | "yes" / "no" / null (null = no bet, edge too small) |
| `bet_size` | Simulated bet in $ |
| `outcome` | 1 = Yes resolved, 0 = No resolved, null = unresolved |
| `pnl` | Computed on resolution |
| `phase` | Which experiment phase produced this evaluation (1, 2, 3…) |
| `timestamp` | Evaluation time |

## Architecture

All logic lives in `app.py` (single file). Structure:

1. **Market fetching** — Polymarket Gamma API, tag + keyword filters, sorted by end date for phase selection
2. **Evaluation engine** — Claude call, returns probability + confidence + reasoning; prompt varies by phase
3. **Bet simulation** — edge calculation, direction, size, stored alongside evaluation
4. **Outcome tracking** — UI to mark resolved markets Yes/No, triggers P&L calculation
5. **Results dashboard** — ROI, calibration chart, per-phase comparison

## Running locally

**With Docker (recommended):**
```bash
cp .env.example .env   # add ANTHROPIC_API_KEY
docker compose up --build
# App available at http://localhost:8501
```

**Without Docker:**
```bash
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-... streamlit run app.py
```

## Deployment

Push to GitHub and connect to Render or Railway using the Docker build type. Set `ANTHROPIC_API_KEY` as an environment variable.
