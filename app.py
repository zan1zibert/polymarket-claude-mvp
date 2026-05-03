import streamlit as st
import os
import json
import re
from datetime import datetime
import sqlite3
import pandas as pd
import requests
from anthropic import Anthropic

st.set_page_config(page_title="Polymarket Claude Evaluator", layout="wide")
st.title("Polymarket LLM Evaluator")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    st.error("ANTHROPIC_API_KEY not found in environment variables.")
    st.stop()

POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"
DB_FILE = "data/evaluations.db"


# === DB ===

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
        data["yes_price"], data["claude_prob_yes"], data["confidence"], data["reasoning"]
    ))
    conn.commit()
    conn.close()


def load_evaluation(market_id):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM evaluations WHERE market_id = ?", conn, params=(market_id,))
    conn.close()
    return df.iloc[0].to_dict() if not df.empty else None


# === MARKET FETCHING ===

SORT_OPTIONS = {
    "Volume (total)": "volume",
    "Volume (24h)": "volume24hr",
    "Liquidity": "liquidity",
    "End date (soonest)": "end_date_asc",
}

_DEMO_MARKETS = pd.DataFrame([{
    "id": "demo1",
    "title": "Demo: Will BTC hit 100k in 2026?",
    "description": "",
    "yes_price": 55.0,
    "volume_24h": 0,
    "volume_total": 0,
    "end_date": "2026-12-31",
    "liquidity": 0,
}])


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_tags() -> list[dict]:
    """Returns all Polymarket tags sorted alphabetically by label."""
    try:
        resp = requests.get(f"{POLYMARKET_GAMMA_BASE}/tags", timeout=10)
        resp.raise_for_status()
        tags = resp.json()
        return sorted(tags, key=lambda t: t["label"].lower())
    except requests.RequestException:
        return []


def _normalize_market(m: dict) -> dict:
    try:
        prices = json.loads(m.get("outcomePrices", "[0.5, 0.5]"))
        yes_price = round(float(prices[0]) * 100, 1)
    except (ValueError, IndexError):
        yes_price = 50.0
    return {
        "id": m.get("conditionId") or m.get("slug"),
        "title": m.get("question") or m.get("title", ""),
        "description": m.get("description", ""),
        "yes_price": yes_price,
        "volume_24h": round(m.get("volume24hr") or 0),
        "volume_total": round(m.get("volumeNum") or 0),
        "end_date": m.get("endDateIso", ""),
        "liquidity": round(m.get("liquidityNum") or 0),
    }


@st.cache_data(ttl=120, show_spinner=False)
def fetch_markets(limit: int, sort: str, keyword: str, tag_slug: str) -> pd.DataFrame:
    """
    Fetches active markets from the Polymarket Gamma API.
    When a tag is selected, uses the /events endpoint (which supports tag filtering)
    and flattens nested markets. Otherwise uses /markets directly.

    Docs: https://docs.polymarket.com/#gamma-markets-api
    """
    
    try:
        if tag_slug:
            # Events endpoint supports tag_slug; markets are nested inside each event
            params = {"active": "true", "closed": "false", "limit": limit, "sort": sort, "tag_slug": tag_slug}
            resp = requests.get(f"{POLYMARKET_GAMMA_BASE}/events", params=params, timeout=10)
            resp.raise_for_status()
            raw_markets = [m for event in resp.json() for m in event.get("markets", [])]
        else:
            # Keyword search needs a bigger pool since filtering is client-side
            fetch_limit = 200 if keyword else limit
            params = {"active": "true", "closed": "false", "limit": fetch_limit, "sort": sort}
            resp = requests.get(f"{POLYMARKET_GAMMA_BASE}/markets", params=params, timeout=10)
            resp.raise_for_status()
            raw_markets = resp.json()

        markets = []
        for m in raw_markets:
            normalized = _normalize_market(m)
            if keyword and keyword.lower() not in normalized["title"].lower():
                continue
            markets.append(normalized)

        # When using events endpoint, cap to the requested limit after flattening
        if tag_slug:
            markets = markets[:limit]

        return pd.DataFrame(markets) if markets else _DEMO_MARKETS
    except requests.RequestException as e:
        st.warning(f"Could not reach Polymarket API ({e}). Showing demo data.")
        return _DEMO_MARKETS


# === CLAUDE ===

client = Anthropic(api_key=ANTHROPIC_API_KEY)


def run_claude_evaluation(market):
    description = market.get("description", "").strip()
    description_block = f"\nResolution criteria:\n{description}" if description else ""

    prompt = f"""You are a sharp prediction market trader. Analyze this market:

Title: {market['title']}
Current Yes price: {market['yes_price']:.1f}¢{description_block}

Output valid JSON only:
{{
  "probability_yes": 65,
  "confidence": 72,
  "reasoning": "Step-by-step...",
  "key_uncertainties": ["list"]
}}"""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}]
    )
    try:
        raw_text = response.content[0].text
        # Extract the JSON object even if Claude wraps it in markdown code fences
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object found in response:\n{raw_text}")
        parsed = json.loads(match.group())
        return {
            "market_id": market["id"],
            "timestamp": datetime.now().isoformat(),
            "title": market["title"],
            "yes_price": market["yes_price"],
            "claude_prob_yes": parsed["probability_yes"],
            "confidence": parsed["confidence"],
            "reasoning": parsed["reasoning"]
        }
    except Exception as e:
        st.error(f"Claude parsing error: {e}")
        return None


# === SIDEBAR: market filter config ===

with st.sidebar:
    st.header("Market Filters")

    tags = fetch_tags()
    tag_options = {"All categories": ""} | {t["label"].title(): t["slug"] for t in tags}
    tag_label = st.selectbox("Category", list(tag_options.keys()))
    tag_slug = tag_options[tag_label]

    limit = st.slider("Number of markets", min_value=5, max_value=50, value=20, step=5)
    sort_label = st.selectbox("Sort by", list(SORT_OPTIONS.keys()))
    sort = SORT_OPTIONS[sort_label]
    keyword = st.text_input("Keyword filter", placeholder="e.g. bitcoin, trump, fed")
    if st.button("Refresh markets"):
        st.cache_data.clear()


# === MAIN LAYOUT ===

col_left, col_right = st.columns([1.2, 2])

with col_left:
    st.subheader("Active Markets")

    with st.spinner("Loading markets..."):
        markets_df = fetch_markets(limit=limit, sort=sort, keyword=keyword, tag_slug=tag_slug)

    display_cols = ["title", "yes_price", "volume_24h", "end_date"]
    selection = st.dataframe(
        markets_df[display_cols].rename(columns={
            "title": "Market",
            "yes_price": "Yes %",
            "volume_24h": "Vol 24h ($)",
            "end_date": "Ends",
        }),
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    selected_row = None
    rows = selection.get("selection", {}).get("rows", [])
    if rows:
        selected_row = markets_df.iloc[rows[0]].to_dict()

with col_right:
    st.subheader("Claude Evaluation")
    if selected_row:
        market_id = selected_row["id"]
        existing = load_evaluation(market_id)

        st.markdown(f"**{selected_row['title']}**")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Market Yes", f"{selected_row['yes_price']}¢")
        col_b.metric("Liquidity", f"${selected_row['liquidity']:,}")
        col_c.metric("Total Volume", f"${selected_row['volume_total']:,}")

        st.divider()

        if st.button("Run Claude Analysis", type="primary"):
            with st.spinner("Calling Claude..."):
                result = run_claude_evaluation(selected_row)
                if result:
                    save_evaluation(result)
                    st.success("Saved to DB")
                    existing = result

        if existing:
            diff = existing["claude_prob_yes"] - existing["yes_price"]
            col1, col2, col3 = st.columns(3)
            col1.metric("Claude Yes", f"{existing['claude_prob_yes']:.1f}%")
            col2.metric("Confidence", f"{existing['confidence']}%")
            col3.metric(
                "Edge vs Market",
                f"{diff:+.1f}%",
                delta_color="normal" if abs(diff) < 5 else "inverse",
            )

            if abs(diff) >= 10:
                direction = "undervalued" if diff > 0 else "overvalued"
                st.warning(f"Significant discrepancy: Claude thinks Yes is {direction} by {abs(diff):.1f}%")

            st.write("**Reasoning**")
            st.markdown(existing["reasoning"])
            st.caption(f"Evaluated: {existing['timestamp']}")
        else:
            st.info("Click above to run a Claude evaluation on this market.")
    else:
        st.info("Select a market on the left to evaluate it.")

st.caption("Data: Polymarket Gamma API · Model: claude-sonnet-4-6 · Persistence: SQLite")
