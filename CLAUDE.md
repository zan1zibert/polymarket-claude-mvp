# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-page Streamlit app that fetches a configurable subset of active Polymarket prediction markets, sends them to Claude for probability evaluation, and persists results in SQLite. It notifies the user where it detects significant discrepancy of what it thinks the the probability should be and what Polymarket thinks it is.

This is foremost a learning project. When developing always recommend and refer to tech docs, blogs and concepts that you think will benefit the author to read to really understand and verify that the project goes in the right direction.

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

Consists of a :

1. Compenent that fetches relevant active markets

2. Component that fetches internal knowledge base that is relevant for each market query

3. Component that sends an evaluation query to Claude

4. Component that persists the final evaluation uses market_id as primary key, polymarket and internal prediction

5. Component that updates the knowledge base

6. UI for configuring relevant active markets and notifies the user of potential lucrative oppotunities


## Deployment

Push to GitHub and connect the repo to Render or Railway using the Docker build type. Set `ANTHROPIC_API_KEY` as an environment variable in the dashboard.
