from __future__ import annotations

import uuid
import numpy as np
import pandas as pd
import yfinance as yf
import streamlit as st

FIELDS = ["Close", "Open", "High", "Low", "Volume"]
OPERATORS = ["Greater than", "Less than", "Crosses above", "Crosses below"]
INDICATORS = [
    "SuperTrend",
    "Ichimoku Cloud Top",
    "Ichimoku Cloud Bottom",
    "Ichimoku Span A",       # unshifted leadingSpanA  = (tenkan + kijun) / 2
    "Ichimoku Span B",       # unshifted leadingSpanB  = (highest + lowest) / 2 over senkou_b
    "EMA",
    "SMA",
    "Number",
]
DEFAULT_PARAMS: dict[str, dict] = {
    "SuperTrend":            {"length": 7,  "multiplier": 3.0},
    "Ichimoku Cloud Top":    {"tenkan": 9,  "kijun": 26, "senkou_b": 52},
    "Ichimoku Cloud Bottom": {"tenkan": 9,  "kijun": 26, "senkou_b": 52},
    "Ichimoku Span A":       {"tenkan": 9,  "kijun": 26, "senkou_b": 52},
    "Ichimoku Span B":       {"tenkan": 9,  "kijun": 26, "senkou_b": 52},
    "EMA":                   {"source": "Close", "period": 200},
    "SMA":                   {"source": "Close", "period": 200},
    "Number":                {"value": 0.0},
}


def new_condition() -> dict:
    return {
        "id": str(uuid.uuid4())[:8],
        "field": "Close",
        "operator": "Greater than",
        "indicator": "SuperTrend",
        "params": {"length": 7, "multiplier": 3.0},
    }


# ── Indicator value helpers ────────────────────────────────────────────────────

def _supertrend_line(df: pd.DataFrame, length: int, multiplier: float) -> pd.Series:
    high  = df["High"].values.astype(float)
    low   = df["Low"].values.astype(float)
    close = df["Close"].values.astype(float)
    n     = len(close)

    tr = np.maximum(high - low,
         np.maximum(np.abs(high - np.roll(close, 1)),
                    np.abs(low  - np.roll(close, 1))))
    tr[0] = high[0] - low[0]

    atr = np.full(n, np.nan)
    atr[length - 1] = np.mean(tr[:length])
    for i in range(length, n):
        atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length

    hl2    = (high + low) / 2
    raw_up = hl2 + multiplier * atr
    raw_dn = hl2 - multiplier * atr

    upper   = np.full(n, np.nan)
    lower   = np.full(n, np.nan)
    st_line = np.full(n, np.nan)

    for i in range(n):
        if np.isnan(atr[i]):
            continue
        upper[i] = raw_up[i] if (np.isnan(upper[i-1]) or raw_up[i] < upper[i-1] or close[i-1] > upper[i-1]) else upper[i-1]
        lower[i] = raw_dn[i] if (np.isnan(lower[i-1]) or raw_dn[i] > lower[i-1] or close[i-1] < lower[i-1]) else lower[i-1]

        if np.isnan(st_line[i-1]) or st_line[i-1] == upper[i-1]:
            st_line[i] = upper[i] if close[i] <= upper[i] else lower[i]
        else:
            st_line[i] = lower[i] if close[i] >= lower[i] else upper[i]

    return pd.Series(st_line, index=df.index)


def _ichimoku_spans(df: pd.DataFrame, tenkan: int, kijun: int, senkou_b: int):
    """Shifted cloud spans — cloud top/bottom as displayed on TradingView chart."""
    high, low = df["High"], df["Low"]
    t = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    k = (high.rolling(kijun).max()  + low.rolling(kijun).min())  / 2
    span_a = ((t + k) / 2).shift(kijun)
    span_b = ((high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2).shift(kijun)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)
    return cloud_top, cloud_bot


def _ichimoku_unshifted(df: pd.DataFrame, tenkan: int, kijun: int, senkou_b: int):
    """Unshifted leading spans — matches Pine Script's leadingSpanA / leadingSpanB
    without the displacement offset (i.e. what ta.supertrend compares against at
    bar[0] before the chart shifts them 26 bars into the future)."""
    high, low = df["High"], df["Low"]
    t = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2   # conversion line
    k = (high.rolling(kijun).max()  + low.rolling(kijun).min())  / 2   # base line
    span_a = (t + k) / 2                                                # no shift
    span_b = (high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2  # no shift
    return span_a, span_b


def _indicator_series(df: pd.DataFrame, indicator: str, params: dict) -> pd.Series | None:
    try:
        if indicator == "SuperTrend":
            return _supertrend_line(df, int(params["length"]), float(params["multiplier"]))

        if indicator in ("Ichimoku Cloud Top", "Ichimoku Cloud Bottom"):
            top, bot = _ichimoku_spans(df, int(params["tenkan"]), int(params["kijun"]), int(params["senkou_b"]))
            return top if indicator == "Ichimoku Cloud Top" else bot

        if indicator in ("Ichimoku Span A", "Ichimoku Span B"):
            span_a, span_b = _ichimoku_unshifted(df, int(params["tenkan"]), int(params["kijun"]), int(params["senkou_b"]))
            return span_a if indicator == "Ichimoku Span A" else span_b

        if indicator == "EMA":
            return df[params["source"]].ewm(span=int(params["period"]), adjust=False).mean()

        if indicator == "SMA":
            return df[params["source"]].rolling(int(params["period"])).mean()

        if indicator == "Number":
            return pd.Series(float(params["value"]), index=df.index)

    except Exception:
        return None


# ── Condition evaluation ───────────────────────────────────────────────────────

def _compare(a: float, op: str, b: float, a_prev: float, b_prev: float) -> bool:
    if op == "Greater than":
        return a > b
    if op == "Less than":
        return a < b
    if op == "Crosses above":
        return a > b and a_prev <= b_prev
    if op == "Crosses below":
        return a < b and a_prev >= b_prev
    return False


def _eval_condition(df: pd.DataFrame, cond: dict) -> tuple[bool, bool] | None:
    """Returns (passes_today, passes_yesterday) or None if data insufficient."""
    field_s = df[cond["field"]]
    ind_s   = _indicator_series(df, cond["indicator"], cond["params"])
    if ind_s is None:
        return None

    combined = pd.DataFrame({"field": field_s, "ind": ind_s}).dropna()
    if len(combined) < 2:
        return None

    f_today, f_prev = combined["field"].iloc[-1], combined["field"].iloc[-2]
    i_today, i_prev = combined["ind"].iloc[-1],   combined["ind"].iloc[-2]

    passes_today = _compare(f_today, cond["operator"], i_today, f_prev, i_prev)
    passes_yest  = _compare(f_prev,  cond["operator"], i_prev,  combined["field"].iloc[-3] if len(combined) > 2 else f_prev,
                             combined["ind"].iloc[-3]   if len(combined) > 2 else i_prev)
    return passes_today, passes_yest


def inspect_ticker(data: pd.DataFrame, ticker: str, conditions: list[dict], lookback: int = 5) -> tuple[list[dict], pd.DataFrame]:
    """
    Return:
      - per-condition summary rows (today's values + pass/fail)
      - a history DataFrame showing the last `lookback` days for each condition
    """
    df = _ticker_df(data, ticker)
    if df is None:
        return [], pd.DataFrame()

    summary_rows = []
    history_cols: dict[str, pd.Series] = {"Date": df.index[-lookback:]}

    for cond in conditions:
        label   = _cond_label(cond)
        field_s = df[cond["field"]]
        ind_s   = _indicator_series(df, cond["indicator"], cond["params"])

        if ind_s is None:
            summary_rows.append({"Condition": label, "Field (today)": "—",
                                  "Indicator (today)": "no data", "Passes": "⚠️ skip"})
            continue

        combined = pd.DataFrame({"field": field_s, "ind": ind_s}).dropna()
        if len(combined) < 2:
            summary_rows.append({"Condition": label, "Field (today)": "—",
                                  "Indicator (today)": "no data", "Passes": "⚠️ skip"})
            continue

        f_today, f_prev = combined["field"].iloc[-1], combined["field"].iloc[-2]
        i_today, i_prev = combined["ind"].iloc[-1],   combined["ind"].iloc[-2]
        passes = _compare(f_today, cond["operator"], i_today, f_prev, i_prev)

        summary_rows.append({
            "Condition":         label,
            "Field (today)":     round(float(f_today), 4),
            "Indicator (today)": round(float(i_today), 4),
            "Passes":            "✅ Yes" if passes else "❌ No",
        })

        # Build history for last N days
        hist = combined.tail(lookback)
        short_label = label[:30] + "…" if len(label) > 30 else label
        history_cols[f"{short_label} | field"] = hist["field"].round(4).values
        history_cols[f"{short_label} | ind"]   = hist["ind"].round(4).values
        history_cols[f"{short_label} | pass"]  = [
            "✅" if _compare(
                float(hist["field"].iloc[i]),
                cond["operator"],
                float(hist["ind"].iloc[i]),
                float(hist["field"].iloc[i-1]) if i > 0 else float(hist["field"].iloc[i]),
                float(hist["ind"].iloc[i-1])   if i > 0 else float(hist["ind"].iloc[i]),
            ) else "❌"
            for i in range(len(hist))
        ]

    try:
        history_df = pd.DataFrame(history_cols)
        history_df["Date"] = pd.to_datetime(history_df["Date"]).dt.strftime("%b %d")
    except Exception:
        history_df = pd.DataFrame()

    return summary_rows, history_df


def _cond_label(cond: dict) -> str:
    p = cond["params"]
    ind = cond["indicator"]
    if ind == "SuperTrend":
        ind_str = f"SuperTrend({p.get('multiplier')}, {p.get('length')})"   # (factor, atrLen) — Pine Script order
    elif ind in ("Ichimoku Cloud Top", "Ichimoku Cloud Bottom", "Ichimoku Span A", "Ichimoku Span B"):
        ind_str = f"{ind}({p.get('tenkan')}, {p.get('kijun')}, {p.get('senkou_b')})"
    elif ind in ("EMA", "SMA"):
        ind_str = f"{ind}({p.get('source')}, {p.get('period')})"
    else:
        ind_str = f"Number({p.get('value')})"
    return f"{cond['field']} {cond['operator']} {ind_str}"


# ── Data fetching ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def get_sp500_tickers() -> pd.DataFrame:
    import io, requests
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; stock-screener/1.0)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    df = pd.read_html(io.StringIO(resp.text))[0][["Symbol", "Security"]]
    df.columns = ["ticker", "name"]
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    return df


@st.cache_data(ttl=3600)
def fetch_data(tickers: tuple, period: str = "2y") -> pd.DataFrame:
    # auto_adjust=False → actual (unadjusted) OHLCV prices, matching TradingView.
    # SuperTrend is path-dependent: historical ATR values affect current bands.
    # Note: indicator values may still diverge from TradingView for stocks with
    # major corporate actions (spinoffs, reverse splits) if TradingView's data
    # for the volatile event period differs from yfinance's data.
    return yf.download(
        list(tickers), period=period, group_by="ticker",
        auto_adjust=False, progress=False, threads=True,
    )


def _ticker_df(data: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    try:
        df = data[ticker].dropna(how="all")
        # Drop Adj Close column if present (auto_adjust=False includes it)
        df = df.drop(columns=[c for c in df.columns if "Adj" in str(c)], errors="ignore")
        return df if len(df) > 60 else None
    except (KeyError, TypeError):
        return None


# ── Main screener ──────────────────────────────────────────────────────────────

def run_screener(
    data: pd.DataFrame,
    tickers_df: pd.DataFrame,
    conditions: list[dict],
) -> pd.DataFrame:
    if not conditions:
        return pd.DataFrame()

    results = []
    for _, row in tickers_df.iterrows():
        ticker = row["ticker"]
        df = _ticker_df(data, ticker)
        if df is None:
            continue

        # Evaluate every condition — ALL must pass (strict AND).
        # We collect yesterday's results only for "new entry" detection.
        passed_all_today = True
        yest_results: list[bool] = []
        insufficient_data = False

        for cond in conditions:
            out = _eval_condition(df, cond)

            if out is None:                   # can't compute this indicator
                insufficient_data = True
                break

            passes_today, passes_yest = out

            if not passes_today:              # this condition fails → reject immediately
                passed_all_today = False
                break

            yest_results.append(passes_yest)  # only collected when today passes

        if insufficient_data or not passed_all_today:
            continue

        # Reach here only when EVERY condition passed today
        new_entry = not all(yest_results)     # at least one failed yesterday → new signal
        close_today = float(df["Close"].iloc[-1])
        close_yest  = float(df["Close"].iloc[-2])
        pct = (close_today - close_yest) / close_yest * 100

        results.append({
            "Ticker":    ticker,
            "Company":   row["name"],
            "Price":     round(close_today, 2),
            "Change %":  round(pct, 2),
            "New entry": new_entry,
        })

    return pd.DataFrame(results)
