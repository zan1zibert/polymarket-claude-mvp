import streamlit as st
import os
import json
from datetime import datetime
import sqlite3
import pandas as pd
import requests
from anthropic import Anthropic

st.set_page_config(page_title="Polymarket Claude Evaluator", layout="wide")
st.title("🕹️ Polymarket LLM Evaluator (Claude)")

# === SECURE KEY LOADING (Docker + GitHub safe) ===
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    st.error("ANTHROPIC_API_KEY not found in environment variables.")
    st.stop()

POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"

# === DB (persisted via Docker volume) ===
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
            outcome INTEGER DEFAULT NULL
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

# === POLYMARKET + CLAUDE (unchanged logic, just cleaner) ===
def get_active_markets(limit=15):
    try:
        resp = requests.get(f"{POLYMARKET_GAMMA_BASE}/markets", params={"active": True, "limit": limit, "sort": "volume"})
        data = resp.json()
        markets = []
        for m in data.get("data", [])[:limit]:
            markets.append({
                "id": m.get("condition_id") or m.get("slug"),
                "title": m.get("question") or m.get("title"),
                "yes_price": round(m.get("tokens", [{}])[0].get("price", 0.5) * 100, 1),
                "volume": m.get("volume", 0)
            })
        return pd.DataFrame(markets)
    except:
        return pd.DataFrame([{"id": "demo1", "title": "Demo: Will BTC hit 100k in 2026?", "yes_price": 55}])

client = Anthropic(api_key=ANTHROPIC_API_KEY)

def run_claude_evaluation(market):
    prompt = f"""You are a sharp prediction market trader. Analyze this market:

Title: {market['title']}
Current Yes price: {market['yes_price']:.1f}¢

Output valid JSON only:
{{
  "probability_yes": 65,
  "confidence": 72,
  "reasoning": "Step-by-step...",
  "key_uncertainties": ["list"]
}}"""
    response = client.messages.create(
        model="claude-3-7-sonnet-20250219",  # or your latest available
        max_tokens=1500,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}]
    )
    try:
        parsed = json.loads(response.content[0].text)
        return {
            "market_id": market.get("id") or market["title"],
            "timestamp": datetime.now().isoformat(),
            "title": market["title"],
            "yes_price": market["yes_price"],
            "prob_yes": parsed["probability_yes"],
            "confidence": parsed["confidence"],
            "reasoning": parsed["reasoning"]
        }
    except Exception as e:
        st.error(f"Claude parsing error: {e}")
        return None

# === UI (same nice split you wanted) ===
col_left, col_right = st.columns([1.2, 2])

with col_left:
    st.subheader("📋 Active Markets")
    markets_df = get_active_markets()
    selection = st.dataframe(markets_df[["title", "yes_price"]], hide_index=True, use_container_width=True, on_select="single_row")

    selected_row = None
    if len(selection["selection"]["rows"]) > 0:
        idx = selection["selection"]["rows"][0]
        selected_row = markets_df.iloc[idx].to_dict()

with col_right:
    st.subheader("🤖 Claude Evaluation")
    if selected_row:
        market_id = selected_row.get("id") or selected_row["title"]
        existing = load_evaluation(market_id)

        if st.button("🔄 Run Fresh Claude Analysis", type="primary"):
            with st.spinner("Calling Claude..."):
                result = run_claude_evaluation(selected_row)
                if result:
                    save_evaluation(result)
                    st.success("✅ Saved to DB")
                    existing = result

        if existing:
            st.metric("Claude Yes Probability", f"{existing['claude_prob_yes']:.1f}%")
            st.metric("Confidence", f"{existing['confidence']}%")
            st.write("**Reasoning**")
            st.markdown(existing["reasoning"])
            st.caption(f"Evaluated: {existing['timestamp']}")
        else:
            st.info("Click button to evaluate")
    else:
        st.info("Select a market on the left")

st.caption("Dockerized • Open-source safe • Data persisted in evaluations.db")
