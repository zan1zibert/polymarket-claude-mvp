# app.py - Polymarket Claude Evaluator MVP
# Run with: streamlit run app.py

import streamlit as st
import os
import json
from datetime import datetime
import sqlite3
import pandas as pd
import requests
from anthropic import Anthropic

# ----------------- CONFIG -----------------
st.set_page_config(page_title="Polymarket Claude Evaluator", layout="wide")
st.title("🕹️ Polymarket LLM Evaluator (Claude)")

# Secrets / Env (use .streamlit/secrets.toml or env vars)
ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"

# ----------------- DB SETUP (SQLite) -----------------
DB_FILE = "evaluations.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            market_id TEXT PRIMARY KEY,
            timestamp TEXT,
            market_title TEXT,
            yes_price REAL,
            claude_prob_yes REAL,
            confidence INTEGER,
            reasoning TEXT,
            outcome INTEGER DEFAULT NULL  -- 1=Yes, 0=No, later filled
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_evaluation(data: dict):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        INSERT OR REPLACE INTO evaluations 
        (market_id, timestamp, market_title, yes_price, claude_prob_yes, confidence, reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data["market_id"], data["timestamp"], data["title"],
        data["yes_price"], data["prob_yes"], data["confidence"], data["reasoning"]
    ))
    conn.commit()
    conn.close()

def load_evaluation(market_id):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM evaluations WHERE market_id = ?", conn, params=(market_id,))
    conn.close()
    return df.iloc[0].to_dict() if not df.empty else None

# ----------------- POLYMARKET HELPERS -----------------
def get_active_markets(limit=20):
    try:
        resp = requests.get(f"{POLYMARKET_GAMMA_BASE}/markets", params={
            "active": True,
            "limit": limit,
            "sort": "volume"
        })
        data = resp.json()
        # Simplify
        markets = []
        for m in data.get("data", [])[:limit]:
            markets.append({
                "id": m.get("condition_id") or m.get("slug"),
                "title": m.get("question") or m.get("title"),
                "yes_price": m.get("tokens", [{}])[0].get("price", 0.5) * 100,
                "volume": m.get("volume", 0)
            })
        return pd.DataFrame(markets)
    except Exception:
        return pd.DataFrame([{"title": "Demo Market: Will Trump win 2028?", "yes_price": 42, "id": "demo1"}])

# ----------------- CLAUDE EVALUATOR -----------------
client = Anthropic(api_key=ANTHROPIC_API_KEY)

def run_claude_evaluation(market):
    prompt = f"""You are a sharp prediction market trader. Analyze this market carefully.

Title: {market['title']}
Current Yes price: {market['yes_price']:.1f}¢ (implied prob {market['yes_price']:.1f}%)

Provide a structured probability estimate.
Respond in valid JSON only:
{{
  "probability_yes": 65,
  "confidence": 72,
  "reasoning": "Step by step explanation...",
  "key_uncertainties": ["list of risks"]
}}"""

    response = client.messages.create(
        model="claude-3-7-sonnet-20250219",  # or latest available to you
        max_tokens=1500,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}]
    )
    
    try:
        content = response.content[0].text
        parsed = json.loads(content)
        return {
            "market_id": market.get("id"),
            "timestamp": datetime.now().isoformat(),
            "title": market["title"],
            "yes_price": market["yes_price"],
            "prob_yes": parsed["probability_yes"],
            "confidence": parsed["confidence"],
            "reasoning": parsed["reasoning"]
        }
    except Exception as e:
        st.error(f"Parsing error: {e}")
        return None

# ----------------- UI -----------------
col_left, col_right = st.columns([1.2, 2])

with col_left:
    st.subheader("📋 Active Markets")
    markets_df = get_active_markets(limit=15)
    selection = st.dataframe(
        markets_df[["title", "yes_price"]],
        hide_index=True,
        use_container_width=True,
        on_select="single_row",
        selection_mode="single-row"
    )
    
    selected_row = None
    if len(selection["selection"]["rows"]) > 0:
        idx = selection["selection"]["rows"][0]
        selected_row = markets_df.iloc[idx].to_dict()

with col_right:
    st.subheader("🤖 Claude Evaluation")
    if selected_row:
        market_id = selected_row.get("id") or selected_row["title"]
        existing = load_evaluation(market_id)
        
        if st.button("🔄 Run / Refresh Claude Analysis", type="primary"):
            with st.spinner("Calling latest Claude model..."):
                result = run_claude_evaluation(selected_row)
                if result:
                    save_evaluation(result)
                    st.success("Saved!")
                    existing = result
        
        if existing:
            st.metric("Claude Yes Probability", f"{existing['claude_prob_yes']:.1f}%")
            st.metric("Confidence", f"{existing['confidence']}%")
            st.write("**Reasoning**")
            st.markdown(existing["reasoning"])
            st.caption(f"Evaluated: {existing['timestamp']}")
        else:
            st.info("Click the button above to get a Claude evaluation")
    else:
        st.info("Select a market on the left")

st.caption("MVP Starter • Data persisted in evaluations.db • Add RAG/Chroma later")