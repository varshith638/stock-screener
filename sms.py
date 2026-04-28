from __future__ import annotations

import pandas as pd


def _format_message(results: pd.DataFrame, new_only: bool) -> str:
    df = results[results["New entry"]] if new_only else results
    if df.empty:
        return "📈 Stock Screener: No matches found."

    label = "new entries" if new_only else "matches"
    lines = [f"📈 Screener: {len(df)} {label}"]

    for _, row in df.head(10).iterrows():
        flag = "🆕 " if row["New entry"] else ""
        lines.append(f"{flag}{row['Ticker']} ${row['Price']:.2f} ({row['Change %']:+.2f}%)")

    if len(df) > 10:
        lines.append(f"...and {len(df) - 10} more.")

    return "\n".join(lines)


def send_sms(results: pd.DataFrame, new_only: bool, account_sid: str, auth_token: str, from_number: str, to_number: str) -> str:
    """Send screener results via Twilio SMS. Returns the message SID on success."""
    from twilio.rest import Client

    body = _format_message(results, new_only)
    client = Client(account_sid, auth_token)
    msg = client.messages.create(body=body, from_=from_number, to=to_number)
    return msg.sid
