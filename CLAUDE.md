# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This is foremost a learning project. When developing always recommend and refer to tech docs, blogs and concepts that you think will benefit the author to read to really understand and verify that the project goes in the right direction.

Polymarket prices = crowd wisdom + sharp money + liquidity effects.
Claude (especially latest Sonnet/Opus) + real-time tools + RAG = strong independent forecaster that can synthesize news, polls, expert analysis, historical base rates, and resolution rules better than most humans on many events.
The edge lives in calibrated discrepancies:

Large gap between market price and Claude’s true-probability estimate
High Claude confidence
Sufficient liquidity (to avoid slippage)
Reasonable time-to-resolution (to avoid capital lock-up)

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

The SQLite database (`evaluations.db`) is a directory on disk (Docker volume mount) — don't delete it.

## Architecture

I’d build it as a LangGraph agentic workflow (or Polymarket’s official Agents framework) with these layers:

Layer - Purpose

1. Ingestion - Discover active markets, real-time prices, order books

2. Research & Context - Build rich context for each market

3. Prediction Engine - Output calibrated probability + confidence + reasoning

4. Discrepancy Engine - Rank +EV opportunities

5. Persistence & Loop - Store every evaluation + eventual resolution for continuous improvement

6. UI / Alerts - Human oversight + notifications


## Deployment

Push to GitHub and connect the repo to Render or Railway using the Docker build type. Set `ANTHROPIC_API_KEY` as an environment variable in the dashboard.
