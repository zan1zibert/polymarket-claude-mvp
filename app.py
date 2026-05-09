import streamlit as st
import os
import json
import re
from datetime import datetime, date, timedelta
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
BET_SIZE = 10.0
EDGE_THRESHOLD = 10.0  # minimum % edge to simulate a bet
CURRENT_PHASE = 1


# === DB ===

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            market_id TEXT PRIMARY KEY,
            timestamp TEXT,
            market_title TEXT,
            tag TEXT,
            end_date TEXT,
            yes_price REAL,
            claude_prob_yes REAL,
            confidence INTEGER,
            reasoning TEXT,
            bet_direction TEXT DEFAULT NULL,
            bet_size REAL DEFAULT NULL,
            outcome INTEGER DEFAULT NULL,
            pnl REAL DEFAULT NULL,
            phase INTEGER DEFAULT 1
        )
    """)
    # Migrate older DBs that are missing new columns
    existing = {row[1] for row in conn.execute("PRAGMA table_info(evaluations)")}
    for col, definition in [
        ("tag", "TEXT"),
        ("end_date", "TEXT"),
        ("bet_direction", "TEXT DEFAULT NULL"),
        ("bet_size", "REAL DEFAULT NULL"),
        ("pnl", "REAL DEFAULT NULL"),
        ("phase", "INTEGER DEFAULT 1"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE evaluations ADD COLUMN {col} {definition}")
    conn.commit()
    conn.close()

init_db()


def compute_bet(claude_prob: float, yes_price: float) -> tuple[str | None, float | None]:
    edge = claude_prob - yes_price
    if abs(edge) < EDGE_THRESHOLD:
        return None, None
    direction = "yes" if edge > 0 else "no"
    return direction, BET_SIZE


def compute_pnl(direction: str, yes_price: float, outcome: int, bet_size: float) -> float:
    # yes_price is 0-100, convert to 0-1
    entry = yes_price / 100
    if direction == "yes":
        return round((outcome * (1 - entry) - (1 - outcome) * entry) * bet_size, 2)
    else:
        return round(((1 - outcome) * (1 - (1 - entry)) - outcome * (1 - entry)) * bet_size, 2)


def save_evaluation(data: dict):
    direction, size = compute_bet(data["claude_prob_yes"], data["yes_price"])
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        INSERT OR REPLACE INTO evaluations
        (market_id, timestamp, market_title, tag, end_date, yes_price,
         claude_prob_yes, confidence, reasoning, bet_direction, bet_size, phase)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["market_id"], data["timestamp"], data["title"],
        data.get("tag", ""), data.get("end_date", ""),
        data["yes_price"], data["claude_prob_yes"], data["confidence"],
        data["reasoning"], direction, size, data.get("phase", CURRENT_PHASE)
    ))
    conn.commit()
    conn.close()


def resolve_market(market_id: str, outcome: int):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT bet_direction, yes_price, bet_size FROM evaluations WHERE market_id = ?",
        (market_id,)
    ).fetchone()
    if row:
        direction, yes_price, bet_size = row
        pnl = compute_pnl(direction, yes_price, outcome, bet_size) if direction else None
        conn.execute(
            "UPDATE evaluations SET outcome = ?, pnl = ? WHERE market_id = ?",
            (outcome, pnl, market_id)
        )
        conn.commit()
    conn.close()


def load_evaluation(market_id: str) -> dict | None:
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM evaluations WHERE market_id = ?", conn, params=(market_id,))
    conn.close()
    return df.iloc[0].to_dict() if not df.empty else None


def load_all_evaluations() -> pd.DataFrame:
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM evaluations ORDER BY timestamp DESC", conn)
    conn.close()
    return df


# === MARKET FETCHING ===

SORT_OPTIONS = {
    "End date (soonest)": "end_date_asc",
    "Volume (total)": "volume",
    "Volume (24h)": "volume24hr",
    "Liquidity": "liquidity",
}

_DEMO_MARKETS = pd.DataFrame([{
    "id": "demo1",
    "title": "Demo: Will BTC hit 100k in 2026?",
    "description": "",
    "tag": "",
    "yes_price": 55.0,
    "volume_24h": 0,
    "volume_total": 0,
    "end_date": "2026-12-31",
    "liquidity": 0,
}])


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_tags() -> list[dict]:
    try:
        resp = requests.get(f"{POLYMARKET_GAMMA_BASE}/tags", timeout=10)
        resp.raise_for_status()
        return sorted(resp.json(), key=lambda t: t["label"].lower())
    except requests.RequestException:
        return []


def _normalize_market(m: dict, tag_label: str = "") -> dict:
    try:
        prices = json.loads(m.get("outcomePrices", "[0.5, 0.5]"))
        yes_price = round(float(prices[0]) * 100, 1)
    except (ValueError, IndexError):
        yes_price = 50.0
    return {
        "id": m.get("conditionId") or m.get("slug"),
        "title": m.get("question") or m.get("title", ""),
        "description": m.get("description", ""),
        "tag": tag_label,
        "yes_price": yes_price,
        "volume_24h": round(m.get("volume24hr") or 0),
        "volume_total": round(m.get("volumeNum") or 0),
        "end_date": m.get("endDateIso", ""),
        "liquidity": round(m.get("liquidityNum") or 0),
    }


def _apply_experiment_filters(markets: list[dict], experiment_mode: bool) -> list[dict]:
    if not experiment_mode:
        return markets
    today = date.today()
    cutoff = today + timedelta(days=7)
    return [
        m for m in markets
        if m["liquidity"] >= 5000
        and 10 <= m["yes_price"] <= 90
        and m["end_date"] != ""
        and today + timedelta(days=2) <= date.fromisoformat(m["end_date"]) <= cutoff
    ]


@st.cache_data(ttl=120, show_spinner=False)
def fetch_markets(limit: int, sort: str, keyword: str, tag_slug: str, tag_label: str, experiment_mode: bool) -> pd.DataFrame:
    try:
        if tag_slug:
            params = {"active": "true", "closed": "false", "limit": limit, "sort": sort, "tag_slug": tag_slug}
            resp = requests.get(f"{POLYMARKET_GAMMA_BASE}/events", params=params, timeout=10)
            resp.raise_for_status()
            raw_markets = [m for event in resp.json() for m in event.get("markets", [])]
        else:
            fetch_limit = 200 if (keyword or experiment_mode) else limit
            params = {"active": "true", "closed": "false", "limit": fetch_limit, "sort": sort}
            resp = requests.get(f"{POLYMARKET_GAMMA_BASE}/markets", params=params, timeout=10)
            resp.raise_for_status()
            raw_markets = resp.json()

        markets = [_normalize_market(m, tag_label) for m in raw_markets]

        if keyword:
            markets = [m for m in markets if keyword.lower() in m["title"].lower()]

        markets = _apply_experiment_filters(markets, experiment_mode)

        if tag_slug:
            markets = markets[:limit]

        return pd.DataFrame(markets) if markets else _DEMO_MARKETS
    except requests.RequestException as e:
        st.warning(f"Could not reach Polymarket API ({e}). Showing demo data.")
        return _DEMO_MARKETS


# === CLAUDE ===

client = Anthropic(api_key=ANTHROPIC_API_KEY)


def run_claude_evaluation(market: dict) -> dict | None:
    description = market.get("description", "").strip()
    description_block = f"\nResolution criteria:\n{description}" if description else ""

    prompt = f"""You are a superforecaster and prediction market trader. Analyze this market carefully.

Title: {market['title']}
Current Yes price: {market['yes_price']:.1f}¢
Ends: {market.get('end_date', 'unknown')}{description_block}

Reason step by step:
1. Outside view: what base rate applies to this class of event?
2. Inside view: what specific factors adjust that base rate up or down?
3. Market efficiency: why might the current price of {market['yes_price']:.1f}¢ be correct or incorrect?
4. Final estimate.

Output valid JSON only:
{{
  "probability_yes": 65,
  "confidence": 72,
  "reasoning": "Step-by-step reasoning...",
  "key_uncertainties": ["list"]
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}]
    )
    try:
        raw_text = response.content[0].text
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON found in response:\n{raw_text}")
        parsed = json.loads(match.group())
        return {
            "market_id": market["id"],
            "timestamp": datetime.now().isoformat(),
            "title": market["title"],
            "tag": market.get("tag", ""),
            "end_date": market.get("end_date", ""),
            "yes_price": market["yes_price"],
            "claude_prob_yes": parsed["probability_yes"],
            "confidence": parsed["confidence"],
            "reasoning": parsed["reasoning"],
            "phase": CURRENT_PHASE,
        }
    except Exception as e:
        st.error(f"Claude parsing error: {e}")
        return None


# === SIDEBAR ===

with st.sidebar:
    st.header("Market Filters")

    experiment_mode = st.toggle(
        "Experiment mode",
        value=False,
        help="Filters to markets ending in 2–7 days, liquidity >$5k, price 10–90¢"
    )

    tags = fetch_tags()
    tag_options = {"All categories": ("", "")} | {t["label"].title(): (t["slug"], t["label"]) for t in tags}
    tag_label_sel = st.selectbox("Category", list(tag_options.keys()))
    tag_slug, tag_label = tag_options[tag_label_sel]

    limit = st.slider("Number of markets", min_value=5, max_value=50, value=20, step=5)
    sort_label = st.selectbox("Sort by", list(SORT_OPTIONS.keys()))
    sort = SORT_OPTIONS[sort_label]
    keyword = st.text_input("Keyword filter", placeholder="e.g. bitcoin, trump, fed")

    if st.button("Refresh markets"):
        st.cache_data.clear()

    st.divider()
    st.caption(f"Phase {CURRENT_PHASE} · Edge threshold: {EDGE_THRESHOLD}% · Bet size: ${BET_SIZE:.0f}")


# === MAIN LAYOUT ===

tab_markets, tab_resolve, tab_results = st.tabs(["Markets", "Resolve", "Results"])


# --- MARKETS TAB ---

with tab_markets:
    col_left, col_right = st.columns([1.2, 2])

    with col_left:
        st.subheader("Active Markets")
        if experiment_mode:
            st.info("Showing markets ending in 2–7 days with sufficient liquidity.")

        with st.spinner("Loading markets..."):
            markets_df = fetch_markets(
                limit=limit, sort=sort, keyword=keyword,
                tag_slug=tag_slug, tag_label=tag_label,
                experiment_mode=experiment_mode
            )

        display_cols = ["title", "yes_price", "end_date", "liquidity"]
        selection = st.dataframe(
            markets_df[display_cols].rename(columns={
                "title": "Market",
                "yes_price": "Yes %",
                "end_date": "Ends",
                "liquidity": "Liquidity ($)",
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
            col_c.metric("Ends", selected_row['end_date'])

            st.divider()

            if st.button("Run Claude Analysis", type="primary"):
                with st.spinner("Calling Claude..."):
                    result = run_claude_evaluation(selected_row)
                    if result:
                        save_evaluation(result)
                        st.success("Saved to DB")
                        existing = load_evaluation(market_id)

            if existing:
                edge = existing["claude_prob_yes"] - existing["yes_price"]
                direction, _ = compute_bet(existing["claude_prob_yes"], existing["yes_price"])

                col1, col2, col3 = st.columns(3)
                col1.metric("Claude Yes", f"{existing['claude_prob_yes']:.1f}%")
                col2.metric("Confidence", f"{existing['confidence']}%")
                col3.metric("Edge", f"{edge:+.1f}%")

                if direction:
                    st.success(f"Simulated bet: **{direction.upper()} @ {existing['yes_price']}¢** · ${BET_SIZE:.0f} · Phase {existing.get('phase', 1)}")
                else:
                    st.info("Edge below threshold — no bet simulated.")

                st.write("**Reasoning**")
                st.markdown(existing["reasoning"])
                st.caption(f"Evaluated: {existing['timestamp']}")
            else:
                st.info("Click above to run a Claude evaluation on this market.")
        else:
            st.info("Select a market on the left to evaluate it.")


# --- RESOLVE TAB ---

with tab_resolve:
    st.subheader("Resolve Markets")
    st.caption("Mark markets as resolved to record outcomes and calculate P&L.")

    all_evals = load_all_evaluations()
    pending = all_evals[all_evals["outcome"].isna() & all_evals["bet_direction"].notna()]
    no_bet = all_evals[all_evals["outcome"].isna() & all_evals["bet_direction"].isna()]

    if pending.empty:
        st.info("No unresolved bets yet.")
    else:
        st.markdown(f"**{len(pending)} unresolved bet(s):**")
        for _, row in pending.iterrows():
            col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
            col1.write(row["market_title"])
            col2.write(f"{row['bet_direction'].upper()} @ {row['yes_price']}¢")
            col3.write(f"Phase {int(row['phase'])}")
            with col4:
                res_col1, res_col2 = st.columns(2)
                if res_col1.button("Yes", key=f"yes_{row['market_id']}"):
                    resolve_market(row["market_id"], 1)
                    st.rerun()
                if res_col2.button("No", key=f"no_{row['market_id']}"):
                    resolve_market(row["market_id"], 0)
                    st.rerun()

    if not no_bet.empty:
        with st.expander(f"{len(no_bet)} evaluated market(s) with no bet (edge too small)"):
            st.dataframe(no_bet[["market_title", "yes_price", "claude_prob_yes", "end_date"]], hide_index=True)


# --- RESULTS TAB ---

with tab_results:
    st.subheader("Experiment Results")

    all_evals = load_all_evaluations()
    resolved = all_evals[all_evals["outcome"].notna() & all_evals["bet_direction"].notna()].copy()

    if resolved.empty:
        st.info("No resolved bets yet. Resolve some markets in the Resolve tab.")
    else:
        resolved["outcome"] = resolved["outcome"].astype(int)
        resolved["pnl"] = resolved["pnl"].astype(float)
        resolved["brier"] = (resolved["claude_prob_yes"] / 100 - resolved["outcome"]) ** 2

        total_bets = len(resolved)
        total_pnl = resolved["pnl"].sum()
        roi = (total_pnl / (total_bets * BET_SIZE)) * 100
        brier = resolved["brier"].mean()
        win_rate = (resolved["pnl"] > 0).mean() * 100

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total bets", total_bets)
        c2.metric("P&L", f"${total_pnl:+.2f}")
        c3.metric("ROI", f"{roi:+.1f}%")
        c4.metric("Brier score", f"{brier:.3f}", help="Lower is better. Random = 0.25, perfect = 0")

        st.divider()

        # By phase
        st.markdown("**By phase**")
        phase_summary = resolved.groupby("phase").agg(
            bets=("pnl", "count"),
            pnl=("pnl", "sum"),
            brier=("brier", "mean"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100)
        ).round(3)
        st.dataframe(phase_summary, use_container_width=True)

        # By tag
        if resolved["tag"].notna().any() and resolved["tag"].str.len().gt(0).any():
            st.markdown("**By category**")
            tag_summary = resolved.groupby("tag").agg(
                bets=("pnl", "count"),
                pnl=("pnl", "sum"),
                brier=("brier", "mean"),
                win_rate=("pnl", lambda x: (x > 0).mean() * 100)
            ).round(3).sort_values("brier")
            st.dataframe(tag_summary, use_container_width=True)

        # By confidence bucket
        st.markdown("**By confidence level**")
        resolved["confidence_bucket"] = pd.cut(
            resolved["confidence"], bins=[0, 50, 65, 80, 100],
            labels=["<50%", "50–65%", "65–80%", ">80%"]
        )
        conf_summary = resolved.groupby("confidence_bucket", observed=True).agg(
            bets=("pnl", "count"),
            pnl=("pnl", "sum"),
            brier=("brier", "mean"),
        ).round(3)
        st.dataframe(conf_summary, use_container_width=True)

        st.divider()
        st.markdown("**All resolved bets**")
        st.dataframe(
            resolved[["market_title", "tag", "phase", "yes_price", "claude_prob_yes", "bet_direction", "outcome", "pnl", "brier"]]
            .rename(columns={"market_title": "Market", "yes_price": "Mkt %", "claude_prob_yes": "Claude %",
                             "bet_direction": "Bet", "outcome": "Result", "pnl": "P&L", "brier": "Brier"}),
            hide_index=True,
            use_container_width=True,
        )

st.caption("Data: Polymarket Gamma API · Model: claude-sonnet-4-6 · Persistence: SQLite")
