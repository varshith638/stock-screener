from __future__ import annotations

import streamlit as st
import pandas as pd
from screener import (
    FIELDS, OPERATORS, INDICATORS, DEFAULT_PARAMS,
    new_condition, get_sp500_tickers, fetch_data, run_screener,
)
from sms import send_sms

st.set_page_config(page_title="Stock Screener", page_icon="📈", layout="wide")

# ── Session state init ────────────────────────────────────────────────────────
if "conditions" not in st.session_state:
    st.session_state.conditions = [new_condition()]


def _init_widget_state(cid: str, ind: str, params: dict) -> None:
    """Pre-populate st.session_state keys for a condition's param widgets
    so Streamlit never falls back to a stale default value."""
    keys = {
        "SuperTrend":            {f"len_{cid}": params.get("length", 7),
                                   f"mul_{cid}": params.get("multiplier", 3.0)},
        "Ichimoku Cloud Top":    {f"ten_{cid}": params.get("tenkan", 9),
                                   f"kij_{cid}": params.get("kijun", 26),
                                   f"sen_{cid}": params.get("senkou_b", 52)},
        "Ichimoku Cloud Bottom": {f"ten_{cid}": params.get("tenkan", 9),
                                   f"kij_{cid}": params.get("kijun", 26),
                                   f"sen_{cid}": params.get("senkou_b", 52)},
        "EMA":                   {f"src_{cid}": params.get("source", "Close"),
                                   f"per_{cid}": params.get("period", 200)},
        "SMA":                   {f"src_{cid}": params.get("source", "Close"),
                                   f"per_{cid}": params.get("period", 200)},
        "Number":                {f"val_{cid}": params.get("value", 0.0)},
    }
    for k, v in keys.get(ind, {}).items():
        if k not in st.session_state:
            st.session_state[k] = v


def _read_params_from_state(cid: str, ind: str) -> dict:
    """Read the current param values straight from st.session_state widget keys."""
    if ind == "SuperTrend":
        return {"length": st.session_state.get(f"len_{cid}", 7),
                "multiplier": st.session_state.get(f"mul_{cid}", 3.0)}
    if ind in ("Ichimoku Cloud Top", "Ichimoku Cloud Bottom"):
        return {"tenkan": st.session_state.get(f"ten_{cid}", 9),
                "kijun":  st.session_state.get(f"kij_{cid}", 26),
                "senkou_b": st.session_state.get(f"sen_{cid}", 52)}
    if ind in ("EMA", "SMA"):
        return {"source": st.session_state.get(f"src_{cid}", "Close"),
                "period": st.session_state.get(f"per_{cid}", 200)}
    if ind == "Number":
        return {"value": st.session_state.get(f"val_{cid}", 0.0)}
    return {}


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📈 Stock Screener")
    st.caption("S&P 500 · Daily · Signal delta vs previous close")

    direction_filter = st.radio(
        "Show",
        ["All matches", "New entries only"],
        help="New entries = stocks that didn't pass all conditions yesterday but do today.",
    )

    st.divider()

    with st.expander("SMS Alerts (Twilio)"):
        _sec = st.secrets if hasattr(st, "secrets") else {}
        st.text_input("Account SID",  value=_sec.get("TWILIO_SID", ""),   type="password", key="twilio_sid")
        st.text_input("Auth Token",   value=_sec.get("TWILIO_TOKEN", ""), type="password", key="twilio_token")
        st.text_input("From number",  value=_sec.get("TWILIO_FROM", ""),  placeholder="+15550001234", key="twilio_from")
        st.text_input("Your number",  value=_sec.get("TWILIO_TO", ""),    placeholder="+15550005678", key="twilio_to")
        st.checkbox("Send new entries only", value=True, key="sms_new_only")

    st.divider()
    run_btn = st.button("Run Screener", type="primary", use_container_width=True)
    if st.button("Clear cache & results", use_container_width=True):
        st.cache_data.clear()
        st.session_state.pop("results", None)
        st.rerun()

# ── Condition builder ─────────────────────────────────────────────────────────
st.title("Condition Builder")
st.caption("Stock must pass **all** conditions below (AND logic).")

conditions = st.session_state.conditions
to_delete  = None

for idx, cond in enumerate(conditions):
    cid = cond["id"]

    # Ensure widget state is seeded for this condition
    _init_widget_state(cid, cond["indicator"], cond["params"])

    c1, c2, c3, c4, c5 = st.columns([1.4, 1.6, 2.2, 3.5, 0.3])

    with c1:
        cond["field"] = st.selectbox(
            "Field", FIELDS, index=FIELDS.index(cond["field"]),
            key=f"field_{cid}", label_visibility="collapsed",
        )
    with c2:
        cond["operator"] = st.selectbox(
            "Operator", OPERATORS, index=OPERATORS.index(cond["operator"]),
            key=f"op_{cid}", label_visibility="collapsed",
        )
    with c3:
        prev_ind = cond["indicator"]
        cond["indicator"] = st.selectbox(
            "Indicator", INDICATORS, index=INDICATORS.index(cond["indicator"]),
            key=f"ind_{cid}", label_visibility="collapsed",
        )
        # If indicator type changed, reset params and re-seed widget state
        if cond["indicator"] != prev_ind:
            cond["params"] = dict(DEFAULT_PARAMS[cond["indicator"]])
            _init_widget_state(cid, cond["indicator"], cond["params"])

    with c4:
        ind = cond["indicator"]
        sources = [f for f in FIELDS if f != "Volume"]

        if ind == "SuperTrend":
            pa, pb = st.columns(2)
            pa.number_input("Length",     min_value=1,   step=1,   key=f"len_{cid}", label_visibility="collapsed")
            pb.number_input("Multiplier", min_value=0.1, step=0.5, key=f"mul_{cid}", label_visibility="collapsed")

        elif ind in ("Ichimoku Cloud Top", "Ichimoku Cloud Bottom"):
            pa, pb, pc = st.columns(3)
            pa.number_input("Tenkan",   min_value=1, step=1, key=f"ten_{cid}", label_visibility="collapsed")
            pb.number_input("Kijun",    min_value=1, step=1, key=f"kij_{cid}", label_visibility="collapsed")
            pc.number_input("Senkou B", min_value=1, step=1, key=f"sen_{cid}", label_visibility="collapsed")

        elif ind in ("EMA", "SMA"):
            pa, pb = st.columns([1.2, 1])
            pa.selectbox("Source", sources,
                         index=sources.index(st.session_state.get(f"src_{cid}", "Close")),
                         key=f"src_{cid}", label_visibility="collapsed")
            pb.number_input("Period", min_value=1, step=1, key=f"per_{cid}", label_visibility="collapsed")

        elif ind == "Number":
            st.number_input("Value", step=1.0, key=f"val_{cid}", label_visibility="collapsed")

    with c5:
        if st.button("✕", key=f"del_{cid}", help="Remove condition", use_container_width=True):
            to_delete = idx

    # Always sync params from widget state so run_screener gets the live values
    cond["params"] = _read_params_from_state(cid, cond["indicator"])

if to_delete is not None:
    st.session_state.conditions.pop(to_delete)
    st.rerun()

col_add, _ = st.columns([1, 5])
if col_add.button("＋ Add condition"):
    st.session_state.conditions.append(new_condition())
    st.rerun()

# Active conditions summary
with st.expander("Active conditions being evaluated", expanded=False):
    for i, cond in enumerate(st.session_state.conditions, 1):
        p = cond["params"]
        if cond["indicator"] == "SuperTrend":
            ind_str = f"SuperTrend({p.get('length')}, {p.get('multiplier')})"
        elif cond["indicator"] in ("Ichimoku Cloud Top", "Ichimoku Cloud Bottom"):
            ind_str = f"{cond['indicator']}({p.get('tenkan')}, {p.get('kijun')}, {p.get('senkou_b')})"
        elif cond["indicator"] in ("EMA", "SMA"):
            ind_str = f"{cond['indicator']}({p.get('source')}, {p.get('period')})"
        else:
            ind_str = f"Number({p.get('value')})"
        st.markdown(f"**{i}.** `{cond['field']}` **{cond['operator']}** `{ind_str}`")

st.divider()

# ── Run & results ─────────────────────────────────────────────────────────────
if run_btn:
    if not st.session_state.conditions:
        st.warning("Add at least one condition before running.")
        st.stop()

    with st.status("Running screener…", expanded=True) as status:
        st.write("Fetching S&P 500 ticker list…")
        tickers_df = get_sp500_tickers()
        tickers = tuple(tickers_df["ticker"].tolist())

        st.write(f"Downloading price data for {len(tickers)} stocks…")
        data = fetch_data(tickers)

        st.write("Evaluating conditions…")
        results = run_screener(data, tickers_df, st.session_state.conditions)
        status.update(label="Done.", state="complete")

    st.session_state["results"] = results
    st.session_state["total_screened"] = len(tickers)

if "results" in st.session_state:
    results: pd.DataFrame = st.session_state["results"]
    total = st.session_state.get("total_screened", "—")

    if direction_filter == "New entries only":
        results = results[results["New entry"] == True]

    new_count = int(results["New entry"].sum()) if not results.empty else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("Stocks screened", total)
    m2.metric("Passing all conditions", len(results))
    m3.metric("New entries (delta)", new_count)

    if results.empty:
        st.info("No stocks match all conditions.")
    else:
        def _style(df: pd.DataFrame):
            def row_style(row):
                if row["New entry"]:
                    return ["background-color: #fff3cd"] * len(row)
                return [""] * len(row)
            return (
                df.style
                .apply(row_style, axis=1)
                .format({"Price": "${:.2f}", "Change %": "{:+.2f}%"})
            )

        display = results.copy()
        display["New entry"] = display["New entry"].map({True: "🆕 Yes", False: ""})
        st.dataframe(_style(display), use_container_width=True, hide_index=True)

        col_csv, col_sms = st.columns(2)
        col_csv.download_button(
            "⬇ Download CSV", results.to_csv(index=False),
            file_name="screener_results.csv", mime="text/csv",
            use_container_width=True,
        )
        twilio_ready = all([
            st.session_state.get("twilio_sid"),
            st.session_state.get("twilio_token"),
            st.session_state.get("twilio_from"),
            st.session_state.get("twilio_to"),
        ])
        if col_sms.button("📱 Send SMS", disabled=not twilio_ready, use_container_width=True,
                          help="Fill in Twilio credentials in the sidebar to enable."):
            with st.spinner("Sending SMS…"):
                try:
                    sid = send_sms(
                        results,
                        new_only=st.session_state.get("sms_new_only", True),
                        account_sid=st.session_state["twilio_sid"],
                        auth_token=st.session_state["twilio_token"],
                        from_number=st.session_state["twilio_from"],
                        to_number=st.session_state["twilio_to"],
                    )
                    st.success(f"SMS sent! Message SID: `{sid}`")
                except Exception as e:
                    st.error(f"SMS failed: {e}")
else:
    st.info("Add conditions above and click **Run Screener** in the sidebar.")
    with st.expander("How it works"):
        st.markdown("""
        Build a list of conditions — stock must pass **all** of them (AND logic).

        | Operator | Meaning |
        |---|---|
        | **Greater than** | Field value is currently above the indicator |
        | **Less than** | Field value is currently below the indicator |
        | **Crosses above** | Field crossed above the indicator since yesterday |
        | **Crosses below** | Field crossed below the indicator since yesterday |

        **New entry** (highlighted in yellow) = stock passes all conditions today but didn't yesterday — the delta.
        """)
